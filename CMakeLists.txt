cmake_minimum_required(VERSION 3.8)
project(hackrf_ros)

# Default to C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

#==========================================================#
#                       Dependencies                       #
#==========================================================#
find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclpy REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/HackRfRx.msg"
  "msg/HackRfConfig.msg"
  "srv/UpdateConfig.srv"
  DEPENDENCIES builtin_interfaces std_msgs
 )

ament_export_dependencies(rosidl_default_runtime)

 #ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  hackrf_ros/hackrf_node.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()