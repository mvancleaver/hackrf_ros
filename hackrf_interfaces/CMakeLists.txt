cmake_minimum_required(VERSION 3.8)
project(hackrf_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(nav_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SpectrumStamped.msg"
  "msg/RFDetection.msg"
  "msg/RFDetectionArray.msg"
  "msg/RFEnvironment.msg"
  "msg/RFEmitterEstimate.msg"
  "msg/RFEmitterMap.msg"
  "srv/Sweep.srv"
  "action/Sweep.action"
  "action/RecordIQ.action"
  DEPENDENCIES std_msgs nav_msgs
)

ament_package()
