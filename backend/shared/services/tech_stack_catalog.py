"""The categories a tech stack is made of, and what Agent Studio suggests for each.

One source for both ends: the router validates category ids against CATEGORIES and serves
`catalog()` to the editor, whose chip inputs suggest SUGGESTIONS while accepting anything
typed. A stack names what the organisation actually uses, not what this list knows.
"""
from __future__ import annotations

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("languages", "Languages"),
    ("backend_frameworks", "Backend frameworks"),
    ("frontend_frameworks", "Frontend frameworks"),
    ("databases", "Databases"),
    ("cloud_hosting", "Cloud & hosting"),
    ("messaging", "Messaging & integration"),
    ("devops", "CI/CD & DevOps"),
    ("testing", "Testing"),
    ("observability", "Observability"),
)
CATEGORY_IDS: tuple[str, ...] = tuple(cid for cid, _ in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "languages": ("Java", "Kotlin", "TypeScript", "JavaScript", "Python", "C#", "Go", "Rust", "Scala",
                  "Ruby", "PHP", "SQL"),
    "backend_frameworks": ("Spring Boot", "Quarkus", "Micronaut", "Node.js", "Express", "NestJS", "FastAPI",
                           "Django", "Flask", ".NET", "ASP.NET Core", "Gin", "Ruby on Rails", "Laravel"),
    "frontend_frameworks": ("React", "Next.js", "Angular", "Vue", "Nuxt", "Svelte", "Tailwind CSS", "Material UI"),
    "databases": ("PostgreSQL", "MySQL", "SQL Server", "Oracle", "MongoDB", "Cosmos DB", "DynamoDB", "Redis",
                  "Elasticsearch", "Cassandra", "SQLite"),
    "cloud_hosting": ("Azure", "AWS", "Google Cloud", "Azure App Service", "Azure Kubernetes Service",
                      "Azure Functions", "AWS Lambda", "Amazon EKS", "Kubernetes", "Docker", "On-premises"),
    "messaging": ("Kafka", "RabbitMQ", "Azure Service Bus", "Azure Event Hubs", "Amazon SQS", "Amazon SNS",
                  "Google Pub/Sub", "REST", "GraphQL", "gRPC"),
    "devops": ("GitHub Actions", "Azure DevOps Pipelines", "GitLab CI", "Jenkins", "Terraform", "Bicep", "Helm",
               "Argo CD"),
    "testing": ("JUnit", "Mockito", "Jest", "Vitest", "Playwright", "Cypress", "pytest", "k6", "Postman"),
    "observability": ("OpenTelemetry", "Prometheus", "Grafana", "Azure Monitor", "Application Insights",
                      "Datadog", "ELK", "Sentry"),
}


def catalog() -> dict:
    return {"categories": [
        {"id": cid, "label": label, "suggestions": list(SUGGESTIONS.get(cid, ()))}
        for cid, label in CATEGORIES
    ]}
