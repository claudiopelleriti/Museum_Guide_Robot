# Design of an Intelligent Robotic Museum Guide ("Sophia")

This repository contains the project developed for the Artificial Intelligence 2 and Robotics courses at the University of Palermo. The project consists of a Unity simulation of an intelligent guide robot, named "Sophia", designed to operate in a real museum context and enhance the visitor's cultural experience.

## Project Objectives
* **Autonomous interaction**: Ability to converse with the user in natural language using an LLM.
* **Personalized paths**: Generation of tours based on the visitor's specific requests and needs.
* **Safe navigation**: Autonomous movement within museum rooms while avoiding dynamic and static obstacles.

## Technologies Used
The project architecture is based on the integration of several software modules:
* **Unity**: 3D simulation environment for the museum and the robot.
* **LLM (Llama)**: Language model for intelligent dialogue management, explanation generation, and Question Answering (QA).
* **Neo4j**: Graph database used to structure the museum's Knowledge Base.
* **Python**: Language used for backend logic and module integration.

## Core Features and Architecture

### 1. Profiling and Dynamic Pathfinding
When a user interacts with Sophia, the robot collects fundamental information such as name, age, available time, and interests. Based on this data, Sophia enters the `PLANNING` state to calculate the best possible museum route. The path can be dynamically updated (`PATH_UPDATE`) in real-time if the visitor makes new requests during the tour.

### 2. Knowledge Graph and LLM Querying
The knowledge base is modeled through an ontological graph in Neo4j using the Cypher language. It connects entities such as Artwork, Artist, Art Movement, and Room. The LLM system is tasked with querying this database to dynamically extract the necessary information to present the artworks.

### 3. Navigation and Kalman Filter
The robot's movement is goal-oriented, aimed at reaching the calculated points of interest. To make the movement simulation realistic, a Kalman Filter was implemented to estimate localization uncertainty, ensuring robust positioning. Furthermore, using LiDAR perceptual sensors, the robot detects and avoids sudden obstacles, such as moving visitors.

### 4. State and Battery Management
The robot's behavior is managed through states that include `MOVING`, `EXPLAINING`, and `QA`. There is also an energy management system: if the battery runs low during a visit, the robot is programmed to complete the current tour before entering the `CHARGING` state and autonomously heading to the charging station.

### 5. Explainability (XAI)
To increase user trust, an Explainability module was integrated. Sophia is able to explain why certain artworks were chosen based on the visitor's profile or illustrate why two artworks are similar, transforming the interaction into a coherent narrative experience.

---
## Authors
Project realized by: Claudio Pelleriti, Giacomo Barone, Andrea Garuccio, and Laura Zanghì.
