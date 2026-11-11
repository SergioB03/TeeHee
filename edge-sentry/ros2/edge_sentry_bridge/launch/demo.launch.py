"""Run the simulator and the autonomy as two ROS 2 nodes.

    ros2 launch edge_sentry_bridge demo.launch.py sim:=/path/to/edge-sim node:=/path/to/edge-node
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("sim", default_value="edge-sim", description="path to the edge-sim program"),
        DeclareLaunchArgument("node", default_value="edge-node", description="path to the edge-node program"),
        DeclareLaunchArgument("period", default_value="0.25", description="seconds per simulation tick"),
        DeclareLaunchArgument("ticks", default_value="0", description="stop after this many ticks; 0 runs until stopped"),
        Node(package="edge_sentry_bridge", executable="autonomy_node", output="screen",
             parameters=[{"node": LaunchConfiguration("node")}]),
        Node(package="edge_sentry_bridge", executable="sim_node", output="screen",
             parameters=[{"sim": LaunchConfiguration("sim"), "period": LaunchConfiguration("period"),
                          "ticks": LaunchConfiguration("ticks")}]),
    ])
