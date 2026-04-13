FROM ros:humble

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-tk \
    libusb-1.0-0-dev \
    libhackrf-dev \
    hackrf \
    ros-humble-diagnostic-updater \
    ros-humble-diagnostic-msgs \
    ros-humble-lifecycle-msgs \
    ros-humble-lifecycle \
    ros-humble-sensor-msgs \
    ros-humble-tf2-ros \
    ros-humble-geometry-msgs \
    ros-humble-nav-msgs \
    ros-humble-rmw-cyclonedds-cpp \
    ros-humble-rqt \
    ros-humble-rqt-common-plugins \
    ros-humble-rqt-topic \
    ros-humble-rqt-plot \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir pyhackrf2 "numpy<2" matplotlib scipy sigmf

ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Copy both packages
RUN mkdir -p /ws/src/hackrf_ros /ws/src/hackrf_interfaces

COPY hackrf_interfaces/ /ws/src/hackrf_interfaces/

COPY hackrf_ros/ /ws/src/hackrf_ros/hackrf_ros/
COPY launch/    /ws/src/hackrf_ros/launch/
COPY config/    /ws/src/hackrf_ros/config/
COPY resource/  /ws/src/hackrf_ros/resource/
COPY package.xml setup.py setup.cfg plugin.xml /ws/src/hackrf_ros/

# Build interfaces first, then driver
SHELL ["/bin/bash", "-c"]
RUN source /opt/ros/humble/setup.bash && \
    cd /ws && \
    colcon build --packages-select hackrf_interfaces && \
    source install/setup.bash && \
    colcon build --packages-select hackrf_ros && \
    rm -rf build log

RUN echo 'source /opt/ros/humble/setup.bash' >> /ros_entrypoint.sh
RUN sed -i '/^exec "\$@"/i source /ws/install/local_setup.bash' /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["ros2", "run", "hackrf_ros", "hackrf_node"]
