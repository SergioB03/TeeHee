cmake_minimum_required(VERSION 3.8)
project(edge_sentry_msgs)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/OwnUnit.msg"
  "msg/Contact.msg"
  "msg/Observation.msg"
  "msg/Order.msg"
  "msg/Plan.msg"
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
