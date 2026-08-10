import re

def fix_mermaid_labels(diagram: str) -> str:
    lines = diagram.splitlines()
    fixed_lines = []

    # Pattern to match Mermaid node definitions: ID[Label]
    node_pattern = re.compile(r'^(\s*\w+\s*\[)(.*?)(\])')

    for line in lines:
        match = node_pattern.match(line)
        if match:
            prefix, label, suffix = match.groups()
            # Check if label has problematic characters
            if '(' in label or ')' in label:
                label = f'"{label}"' if not (label.startswith('"') and label.endswith('"')) else label
            fixed_line = f"{prefix}{label}{suffix}"
            fixed_lines.append(fixed_line)
        else:
            fixed_lines.append(line)  # Non-node lines (like edges) stay the same

    return "\n".join(fixed_lines)


# Example usage
input_mermaid = """
graph TD
    subgraph External Systems
        OMS[Proprietary Order Management System]
        TrafficAPI[External Traffic APIs]
        WeatherAPI[External Weather Services]
    end

    subgraph SwiftRoute Platform
        subgraph Client Applications
            DriverApp[Driver Mobile Application]
            CustomerPortal[Customer Web Portal]
            DispatcherUI[Dispatcher Web UI]
        end

        IntegrationGW(API Gateway / Load Balancer)

        subgraph Backend Services
            ROEngine(Intelligent Route Optimization Engine)
            FMS_Service(Fleet Management Service)
            DeliveryService(Delivery Management Service)
            NotificationService(Notification Service)
            AuthService(Authentication & Authorization Service)
            DataIngestion(Data Ingestion Service)
        end

        DB[(Primary Database)]
        KVStore[Key-Value Store / Cache]
        MessageBroker((Message Broker))
        Storage[Object Storage (e.g., for PoD)]
    end

    OMS -- Pull Orders, Push Status --> IntegrationGW
    TrafficAPI -- Real-time Traffic Data --> IntegrationGW
    WeatherAPI -- Real-time Weather Data --> IntegrationGW

    ClientApplications --> IntegrationGW
    IntegrationGW -- Secure API Calls --> BackendServices

    ROEngine -- Optimizes Routes --> FMS_Service
    ROEngine -- Consumes --> DataIngestion
    DataIngestion -- Feeds --> ROEngine
    DataIngestion -- Feeds --> FMS_Service
    DriverApp -- Updates Status, GPS --> DeliveryService
    DeliveryService -- Updates --> FMS_Service
    DeliveryService -- Triggers --> NotificationService
    FMS_Service -- Real-time Tracking, Re-routing --> DriverApp
    FMS_Service -- Real-time Tracking --> CustomerPortal
    FMS_Service -- Fleet Management --> DispatcherUI

    BackendServices -- Read/Write --> DB
    BackendServices -- Cache --> KVStore
    BackendServices -- Events/Async Tasks --> MessageBroker
    DriverApp -- Upload Proof of Delivery --> Storage
    DeliveryService -- Stores PoD Metadata --> DB
    DeliveryService -- Retrieves PoD --> Storage

    MessageBroker -- Alerts --> NotificationService
    NotificationService -- Send ETA, Status --> CustomerPortal
    NotificationService -- Send Dispatch --> DriverApp
    NotificationService -- Alerts --> DispatcherUI
"""

fixed_output = fix_mermaid_labels(input_mermaid)
print(fixed_output)
