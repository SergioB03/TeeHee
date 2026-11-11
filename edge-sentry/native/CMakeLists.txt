cmake_minimum_required(VERSION 3.16)
# Prefer g++ — clang on this image may fail to find -lstdc++.
if(NOT CMAKE_CXX_COMPILER)
  set(CMAKE_CXX_COMPILER g++)
endif()
project(edge_sim LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(edge-sim
  src/main.cpp
  src/world.cpp
  src/aiml_scorer.cpp
  src/autonomy.cpp
)

# The autonomy planner on its own, for a small computer on the network. It
# needs nothing from the simulator: only the planner and the message format.
add_executable(edge-node
  node/edge_node.cpp
  src/autonomy.cpp
)
target_include_directories(edge-node PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

target_include_directories(edge-sim PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

install(TARGETS edge-sim RUNTIME DESTINATION bin)