"""Small runtime checks using in-memory actors; no CARLA server is started."""
import csv
import importlib.util
from pathlib import Path
import queue
import tempfile
import types
import unittest


@unittest.skipUnless(importlib.util.find_spec('carla'), 'run with the CARLA environment')
class SceneRuntimeTests(unittest.TestCase):
    def make_simulator(self, root):
        import carla
        from helpers.continuous import CARLASimulator
        from helpers.geometry import build_camera_frustum
        class Actor:
            type_id = 'vehicle.tesla.model3'
            def __init__(self, world, transform, actor_id):
                self.world,self.transform,self.id = world,transform,actor_id
                self.velocity=carla.Vector3D()
            def set_target_velocity(self,v): self.velocity=v
            def get_velocity(self):return self.velocity
            def get_transform(self):return self.transform
            def destroy(self):del self.world.actors[self.id]
        class World:
            def __init__(self,queues):self.queues=queues;self.actors={};self.frame=0;self.next_id=1;self.max_cars=0
            def tick(self):
                self.frame+=1
                for actor in self.actors.values():actor.transform.location.x+=actor.velocity.x/10
                for q in self.queues:q.put(types.SimpleNamespace(frame=self.frame,raw_data=bytes(16),height=2,width=2))
                return self.frame
            def spawn_actor(self,bp,transform):
                actor=Actor(self,transform,self.next_id);self.next_id+=1
                self.actors[actor.id]=actor;self.max_cars=max(self.max_cars,len(self.actors));return actor
            def apply_settings(self,settings):pass
        class Writer:
            def __init__(self):self.frames=0;self.closed=False
            def write_frame(self,frame):self.frames+=1
            def close(self):self.closed=True
        sim=CARLASimulator.__new__(CARLASimulator)
        sim.fps=10;sim.record_fov=True;sim.maintain_velocity=True
        sim.exit_delta_frames=3;sim.group_gap_frames=3;sim.stop_when_empty=True
        sim.last_despawn_frame=None;sim.group_offsets={};sim.pedestrians=None
        sim.cam_models=[build_camera_frustum((0,0,0),0,0,0,120,2,2)]*4
        sim.csv_path=str(root/'trajectory.csv')
        Path(sim.csv_path).write_text('frame,timestamp,vehicle_id,vehicle_type,x,y,z,vx,vy,vz,vehicle_index,yaw,pitch,roll,fov_states\n')
        sim.static_cam_queues=[queue.Queue() for _ in range(4)]
        sim.segmented_writers=[Writer() for _ in range(4)];sim.static_cameras=[]
        sim.world=World(sim.static_cam_queues);sim.settings=types.SimpleNamespace(synchronous_mode=True)
        sim.vehicle_configs=[];sim.active_vehicles={};sim.despawn_queue={};sim.next_spawn_idx=0
        sim.blueprint_library=types.SimpleNamespace(filter=lambda name:[types.SimpleNamespace(has_attribute=lambda name:False)])
        return sim

    def add_car(self,sim,index,start,group,travel_frames):
        class Driver:
            def __init__(self,actor):self.actor=actor;self.birth=sim.world.frame
            def arrived(self):return sim.world.frame>=self.birth+travel_frames
            def step(self):pass
        sim.schedule_vehicle('vehicle.tesla.model3',(1,0),(10,0),0,start/10,1,
            spawn_frame=start,despawn_frame=start+10,vehicle_index=index,group_index=group,
            driver_factory=Driver,timeout_frames=50)

    def test_each_agent_controls_its_lifetime_and_exits_are_staggered(self):
        with tempfile.TemporaryDirectory() as directory:
            sim=self.make_simulator(Path(directory))
            self.add_car(sim,0,0,0,20);self.add_car(sim,1,3,0,17)
            sim.run_simulation(10)
            a,b=sim.vehicle_configs
            self.assertEqual([a['actual_spawn_frame'],b['actual_spawn_frame']],[0,3])
            self.assertEqual([a['actual_despawn_frame'],b['actual_despawn_frame']],[20,23])
            self.assertTrue(all(w.frames==24 and w.closed for w in sim.segmented_writers))
            self.assertFalse(sim.world.actors)

    def test_continuous_groups_wait_for_delayed_agent_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            sim=self.make_simulator(Path(directory))
            self.add_car(sim,0,0,0,20);self.add_car(sim,1,13,1,20)
            sim.run_simulation(10)
            self.assertEqual(sim.world.max_cars,1)
            self.assertEqual(sim.vehicle_configs[1]['actual_spawn_frame'],23)
            self.assertEqual(sim.vehicle_configs[1]['actual_despawn_frame'],43)

    def test_continuous_gap_follows_actual_completion_and_group_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            sim=self.make_simulator(Path(directory))
            self.add_car(sim,0,0,0,20);self.add_car(sim,1,30,1,20)
            sim.vehicle_configs[1]['group_gap_frames']=7
            sim.run_simulation(10)
            self.assertEqual(sim.vehicle_configs[1]['actual_spawn_frame'],27)

    def test_agent_initialization_waits_for_a_spawn_tick(self):
        import carla
        from unittest.mock import Mock, patch
        from helpers.driving import AgentDriver
        actor=Mock()
        actor.get_location.return_value=carla.Location()
        driver=AgentDriver(actor,{'target_x':10,'target_y':0},{'ARRIVAL_RADIUS':1},None)
        agent=Mock()
        agent.done.return_value=False
        agent.run_step.return_value=carla.VehicleControl(throttle=.5)
        def initialize():driver.agent=agent
        with patch.object(driver,'_initialize',side_effect=initialize) as init:
            self.assertFalse(driver.arrived())
            driver.step()
            init.assert_not_called()
            actor.get_location.assert_not_called()
            driver.step()
            init.assert_called_once()
            agent.run_step.assert_called_once()

    def test_stuck_agent_fails_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            sim=self.make_simulator(Path(directory));self.add_car(sim,0,0,0,1000)
            with self.assertRaisesRegex(RuntimeError,'MAX_DURATION'):sim.run_simulation(10)
            self.assertFalse(sim.world.actors)
            self.assertTrue(all(w.closed for w in sim.segmented_writers))
