"""One independent CARLA navigation agent per vehicle."""
import math
import random


class AgentDriver:
    def __init__(self, actor, spec, settings, planner):
        import carla
        self.actor = actor
        self.spec, self.settings, self.planner = spec, settings, planner
        self.target = carla.Location(x=spec['target_x'], y=spec['target_y'], z=0)
        self.radius = settings['ARRIVAL_RADIUS']
        self.agent = None
        self.waiting_for_spawn_tick = True
        self.last_control = carla.VehicleControl(throttle=.3)
        self.failures = 0

    def _initialize(self):
        from agents.navigation.basic_agent import BasicAgent
        from agents.navigation.behavior_agent import BehaviorAgent
        actor, spec, settings, planner = self.actor, self.spec, self.settings, self.planner
        if settings['AGENT_MODE'] == 'behavioragent':
            self.agent = BehaviorAgent(actor, behavior=settings['BEHAVIOR'],
                opt_dict={}, map_inst=actor.get_world().get_map(), grp_inst=planner)
            # BehaviorAgent otherwise replaces the requested speed in run_step.
            self.agent._behavior.max_speed = min(self.agent._behavior.max_speed, spec['speed'] * 3.6)
            # Match the old pedestrian scenario: walkers do not cross roads and
            # the behavior agent's pedestrian-avoidance override remains disabled.
            self.agent.pedestrian_avoid_manager = lambda waypoint: (False, None, -1)
        else:
            self.agent = BasicAgent(actor, opt_dict={},
                map_inst=actor.get_world().get_map(), grp_inst=planner)
        self.agent.set_target_speed(spec['speed'] * 3.6)
        self.agent.set_destination(self.target, start_location=actor.get_location())

    def arrived(self):
        return self.agent is not None and (self.actor.get_location().distance(self.target) <= self.radius or self.agent.done())

    def step(self):
        import carla
        if self.waiting_for_spawn_tick:
            self.waiting_for_spawn_tick = False
            self.actor.apply_control(carla.VehicleControl(brake=1))
            return
        if self.agent is None:
            self._initialize()
        if self.arrived():
            self.actor.apply_control(carla.VehicleControl(brake=1))
            return
        try:
            self.last_control = self.agent.run_step()
            self.failures = 0
        except AttributeError:
            # The legacy agents occasionally lose a waypoint at junctions.
            self.failures += 1
            if self.failures % 10 == 0:
                self.agent.set_destination(self.target, start_location=self.actor.get_location())
            if self.failures >= 60:
                raise RuntimeError('Agent repeatedly failed to find its next waypoint')
        self.actor.apply_control(self.last_control)


def calibrate_route(world, spec, settings, planner, cameras, fps=30):
    """Observe a solo route without recording cameras; never extrapolate a turn.

    This profile estimates combined-scene timing. Other cars can change it, so
    actual arrival, exit gaps and visibility are checked by the real recorder.
    """
    import carla
    from helpers.multi_vehicle import NoFeasibleSchedule
    from helpers.visibility import actor_fov_states, longest_window
    original = world.get_settings()
    sync = world.get_settings()
    sync.synchronous_mode = True
    sync.fixed_delta_seconds = 1 / fps
    actor = None
    state = random.getstate()
    try:
        world.apply_settings(sync)
        bp = world.get_blueprint_library().find(spec['vehicle_type'])
        if bp.has_attribute('color'):
            bp.set_attribute('color', spec['vehicle_color'])
        actor = world.try_spawn_actor(bp, carla.Transform(
            carla.Location(x=spec['spawn_x'], y=spec['spawn_y'], z=.3),
            carla.Rotation(yaw=spec['yaw'])))
        if actor is None:
            raise NoFeasibleSchedule('Agent calibration spawn is occupied')
        driver = AgentDriver(actor, spec, settings, planner)
        visible = []
        for _ in range(math.ceil(settings['MAX_DURATION'] * fps)):
            if driver.arrived() and visible:
                return len(visible) / fps, longest_window(visible)
            driver.step()
            world.tick()
            visible.append(any(actor_fov_states(actor, cameras)))
        if driver.arrived():
            return len(visible) / fps, longest_window(visible)
        raise NoFeasibleSchedule('Agent did not finish its calibration route within MAX_DURATION')
    finally:
        if actor is not None:
            actor.destroy()
        world.apply_settings(original)
        random.setstate(state)
