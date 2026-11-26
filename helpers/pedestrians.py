"""Seeded walkers shared by fixed-velocity and agent recordings."""
import random


class Pedestrians:
    def __init__(self, client, world, count, seed):
        self.client, self.world = client, world
        self.walkers = []
        self.controllers = []
        self.count = 0
        if not count:
            return
        import carla
        rng = random.Random(seed)
        world.set_pedestrians_seed(seed)
        world.set_pedestrians_cross_factor(0)
        library = world.get_blueprint_library()
        choices = list(library.filter('walker.pedestrian.*'))
        try:
            speeds = []
            batch = []
            for _ in range(count):
                location = world.get_random_location_from_navigation()
                if location is None:
                    continue
                bp = rng.choice(choices)
                speed = float(bp.get_attribute('speed').recommended_values[1]) if bp.has_attribute('speed') else 0
                speeds.append(speed)
                batch.append(carla.command.SpawnActor(bp, carla.Transform(location)))
            results = client.apply_batch_sync(batch, False)
            successful_speeds = []
            for result, speed in zip(results, speeds):
                if not result.error:
                    self.walkers.append(result.actor_id)
                    successful_speeds.append(speed)
            controller = library.find('controller.ai.walker')
            results = client.apply_batch_sync([
                carla.command.SpawnActor(controller, carla.Transform(), actor_id)
                for actor_id in self.walkers], False)
            active = []
            for result, actor_id, speed in zip(results, list(self.walkers), successful_speeds):
                if result.error:
                    client.apply_batch_sync([carla.command.DestroyActor(actor_id)], False)
                    self.walkers.remove(actor_id)
                    continue
                self.controllers.append(result.actor_id)
                active.append((result.actor_id, speed))
            world.tick()  # materialize controller actors; capture discards this setup frame
            for controller_id, speed in active:
                actor = world.get_actor(controller_id)
                actor.start()
                destination = world.get_random_location_from_navigation()
                if destination is not None:
                    actor.go_to_location(destination)
                actor.set_max_speed(speed)
            self.count = len(active)
            print(f'Pedestrians: {self.count} spawned of {count} requested')
            if not self.count:
                raise RuntimeError('No pedestrians could be spawned for this recording')
        except Exception:
            self.cleanup()
            raise

    def cleanup(self):
        import carla
        for actor_id in self.controllers:
            actor = self.world.get_actor(actor_id)
            if actor is not None:
                try:
                    actor.stop()
                except RuntimeError:
                    pass
        ids = self.controllers + self.walkers
        if ids:
            self.client.apply_batch_sync([carla.command.DestroyActor(i) for i in ids], False)
        self.controllers.clear()
        self.walkers.clear()
