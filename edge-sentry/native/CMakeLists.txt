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
)

target_include_directories(edge-sim PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

install(TARGETS edge-sim RUNTIME DESTINATION bin)