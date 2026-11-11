# The ROS 2 bridge in an image, so the demo needs no ROS on your machine.
# From the folder above:
#
#   docker compose --profile ros2 up ros2
#
# The simulator node and the autonomy node run as two processes that talk over
# ROS 2 topics; the demo plays 20 ticks, prints each order with its reason,
# and stops. Built on the official ROS 2 Jazzy image (about 1.5 GB).
FROM ros:jazzy
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /ws
COPY native/ native/
RUN g++ -std=c++17 -O2 -Inative/src native/src/*.cpp -o /usr/local/bin/edge-sim \
 && g++ -std=c++17 -O2 -Inative/src native/node/edge_node.cpp native/src/autonomy.cpp -o /usr/local/bin/edge-node
COPY ros2/ ros2/
RUN source /opt/ros/jazzy/setup.bash \
 && cd ros2 && colcon build \
 && printf '#!/bin/bash\nset -e\nsource /opt/ros/jazzy/setup.bash --\nsource /ws/ros2/install/setup.bash --\nexec "$@"\n' > /usr/local/bin/with-ros \
 && chmod +x /usr/local/bin/with-ros \
 && useradd --create-home --uid 10001 edge
USER edge
# Both nodes are in this one container, so discovery stays on the loopback.
ENV ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
ENTRYPOINT ["with-ros"]
CMD ["ros2", "launch", "edge_sentry_bridge", "demo.launch.py", "ticks:=20"]
