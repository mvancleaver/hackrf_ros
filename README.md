# hackrf_ros
Basic ROS2 Wrapper to allow interacting with HackRF Devices 

### Docker 

This repo includes [`Dockerfile.hackrf_ros`](https://github.com/mvancleaver/hackrf_ros/blob/main/docker/Dockerfile.hackrf_ros)  to build the image and a [`docker-compose.yaml`](https://github.com/mvancleaver/hackrf_ros/blob/main/docker/cookbook/docker-compose.yaml) to run it. 
It includes all publicaly available Hackrf python libraries :

- [pyhackrf](https://pypi.org/project/pyhackrf/) (called in the ros node)
- [pyhackrf2](https://pypi.org/project/pyhackrf2/)
- [python-hackrf](https://pypi.org/project/python-hackrf/)

and is currently built with 

#### Usage

    cd hackrf_ros/docker/cookbook
    docker compose build hackrf_ros

This will spin up the container and launch the `hackrf_node` using the following command:

    ros2 run hackrf_ros hackrf_node.py

### Build from Source 

Refer to the [`Dockerfile.hackrf_ros`](https://github.com/mvancleaver/hackrf_ros/blob/main/docker/Dockerfile.hackrf_ros) to source/install all the dependencies


