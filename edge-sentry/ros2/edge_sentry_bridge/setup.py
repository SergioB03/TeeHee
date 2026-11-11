from glob import glob

from setuptools import setup

package_name = "edge_sentry_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="edge-sentry",
    maintainer_email="noreply@example.com",
    description="Simulator and autonomy planner as ROS 2 nodes.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "sim_node = edge_sentry_bridge.sim_node:main",
            "autonomy_node = edge_sentry_bridge.autonomy_node:main",
        ],
    },
)
