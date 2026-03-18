# Design of an Intelligent Robotic Museum Guide ("Sophia")

[cite_start]This repository contains the project developed for the Artificial Intelligence 2 and Robotics courses at the University of Palermo[cite: 3, 8]. [cite_start]The project consists of a Unity simulation of an intelligent guide robot, named "Sophia" [cite: 29, 118][cite_start], designed to operate in a real museum context and enhance the visitor's cultural experience[cite: 11].

## Project Objectives
* [cite_start]**Autonomous interaction**: Ability to converse with the user in natural language using an LLM[cite: 14, 18].
* [cite_start]**Personalized paths**: Generation of tours based on the visitor's specific requests and needs[cite: 13, 38].
* [cite_start]**Safe navigation**: Autonomous movement within museum rooms while avoiding dynamic and static obstacles[cite: 15, 22, 26, 74].

## Technologies Used
The project architecture is based on the integration of several software modules:
* [cite_start]**Unity**: 3D simulation environment for the museum and the robot[cite: 118].
* [cite_start]**LLM (Llama)**: Language model for intelligent dialogue management, explanation generation, and Question Answering (QA)[cite: 18, 54, 117].
* [cite_start]**Neo4j**: Graph database used to structure the museum's Knowledge Base[cite: 89, 92, 120].
* [cite_start]**Python**: Language used for backend logic and module integration[cite: 119].

## Core Features and Architecture

### 1. Profiling and Dynamic Pathfinding
[cite_start]When a user interacts with Sophia, the robot collects fundamental information such as name, age, available time, and interests[cite: 37, 38]. [cite_start]Based on this data, Sophia enters the `PLANNING` state to calculate the best possible museum route[cite: 48]. [cite_start]The path can be dynamically updated (`PATH_UPDATE`) in real-time if the visitor makes new requests during the tour[cite: 56, 57].

### 2. Knowledge Graph and LLM Querying
[cite_start]The knowledge base is modeled through an ontological graph in Neo4j using the Cypher language[cite: 90, 92]. [cite_start]It connects entities such as Artwork, Artist, Art Movement, and Room[cite: 105, 107]. [cite_start]The LLM system is tasked with querying this database to dynamically extract the necessary information to present the artworks[cite: 101].

### 3. Navigation and Kalman Filter
[cite_start]The robot's movement is goal-oriented, aimed at reaching the calculated points of interest[cite: 73]. [cite_start]To make the movement simulation realistic, a Kalman Filter was implemented to estimate localization uncertainty, ensuring robust positioning[cite: 88]. [cite_start]Furthermore, using LiDAR perceptual sensors, the robot detects and avoids sudden obstacles, such as moving visitors[cite: 34, 70, 71].

### 4. State and Battery Management
[cite_start]The robot's behavior is managed through states that include `MOVING`, `EXPLAINING`, and `QA`[cite: 49, 53, 54]. [cite_start]There is also an energy management system: if the battery runs low during a visit, the robot is programmed to complete the current tour before entering the `CHARGING` state and autonomously heading to the charging station[cite: 61, 63, 64, 65, 66].

### 5. Explainability (XAI)
[cite_start]To increase user trust, an Explainability module was integrated[cite: 109, 114]. [cite_start]Sophia is able to explain why certain artworks were chosen based on the visitor's profile or illustrate why two artworks are similar, transforming the interaction into a coherent narrative experience[cite: 110, 111, 115].

---
## Authors
[cite_start]Project realized by: Claudio Pelleriti, Giacomo Barone, Andrea Garuccio, and Laura Zanghì[cite: 9].
