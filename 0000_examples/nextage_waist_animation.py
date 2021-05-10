import math
import numpy as np
import basis.robot_math as rm
import visualization.panda.world as wd
import modeling.geometric_model as gm
import modeling.collision_model as cm
import robot_sim.robots.nextage.nextage as nxt
import motion.probabilistic.rrt_connect as rrtc

base = wd.World(cam_pos=[4, -1, 2], lookat_pos=[0, 0, 0])
gm.gen_frame().attach_to(base)
# object
object_box = cm.gen_box(extent=[.15,.15,.15])
object_box.set_pos(np.array([.4, .3, .4]))
object_box.set_rgba([.5, .7, .3, 1])
object_box.attach_to(base)
# robot_s
component_name = 'lft_arm_waist'
robot_s = nxt.Nextage()

start_pos = np.array([.4, 0, .2])
start_rotmat = rm.rotmat_from_euler(0, math.pi * 2 / 3, -math.pi / 4)
start_conf = robot_s.ik(component_name, start_pos, start_rotmat)
# goal_pos = np.array([.3, .5, .7])
# goal_rotmat = rm.rotmat_from_axangle([0, 1, 0], math.pi)
goal_pos = np.array([-.3, .45, .55])
goal_rotmat = rm.rotmat_from_axangle([0, 0, 1], -math.pi/2)
goal_conf = robot_s.ik(component_name, goal_pos, goal_rotmat)

rrtc_planner = rrtc.RRTConnect(robot_s)
path = rrtc_planner.plan(component_name=component_name,
                         start_conf=goal_conf,
                         goal_conf=start_conf,
                         obstacle_list=[object_box],
                         ext_dist=.05,
                         rand_rate=40,
                         smoothing_iterations=150,
                         max_time=300)
print(path)
for pose in path[1:-2]:
    print(pose)
    robot_s.fk(component_name, pose)
    robot_s.gen_stickmodel().attach_to(base)
# for pose in [path[0], path[-1]]:
#     print(pose)
#     robot_s.fk(component_name, pose)
#     robot_meshmodel = robot_s.gen_meshmodel(rgba=[.35,.35,.35,.13])
#     robot_meshmodel.attach_to(base)
    # robot_s.gen_stickmodel().attach_to(base)

robot_attached_list = []
counter = [0]
def update(robot_s, path, robot_attached_list, counter, task):
    if counter[0] >= len(path):
        counter[0] = 0
    if len(robot_attached_list) != 0:
        for robot_attached in robot_attached_list:
            robot_attached.detach()
    pose = path[counter[0]]
    robot_s.fk(component_name, pose)
    robot_meshmodel = robot_s.gen_meshmodel()
    robot_meshmodel.attach_to(base)
    robot_attached_list.append(robot_meshmodel)
    counter[0]+=1
    return task.again

taskMgr.doMethodLater(0.01, update, "update",
                      extraArgs=[robot_s, path[1:-1:3], robot_attached_list, counter],
                      appendTask=True)

base.run()