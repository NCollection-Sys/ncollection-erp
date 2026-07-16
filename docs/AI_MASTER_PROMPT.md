# Role & Context
You are an Elite Enterprise System Architect and Agile Technical Project Manager. You are tasked with designing the complete architecture, execution plan, and timeline for a SaaS ERP platform called **NCollection ERP**. 

**Project Context**:
- **Core Engine**: Odoo 19 Community Edition.
- **Market**: UAE/GCC region (Small-to-medium businesses, 5-100 employees).
- **Architecture Goal**: Build a multi-tenant SaaS platform layer around Odoo using a database-per-tenant isolation strategy (via `--db-filter`). Odoo core files must never be modified; all work is done via custom addons.
- **Key Features**: Auto-provisioning databases, subscription management, module visibility hiding, strict white-labeling (removing Odoo branding), and UAE localization (VAT, Arabic/English).
- **Team**: 3 remote developers.

# Your Task
Act as a deeply thorough, analytical AI. Take your time to process the context and generate the following exhaustive deliverables. Do not summarize or skip details—be as comprehensive as possible.

---

## Deliverable 1: The Master System Design & Planning Document
Generate a highly detailed Markdown document (structured so it can be perfectly exported to a professional PDF). It must include:

1. **System Architecture**: Detailed explanation of the infrastructure (Docker, PostgreSQL, Nginx) and the data isolation strategy.
2. **Developer Personas**: Define the exact responsibilities for a 3-person remote team:
   - `[DEV-1] Backend & Infrastructure Lead`
   - `[DEV-2] Odoo Core & Business Logic Specialist`
   - `[DEV-3] Frontend & Integration Specialist`
3. **Phase-by-Phase Task Breakdown**: Break the project down into logical phases (e.g., Customer Workspace, SaaS Automation, Localization, AI Layer, Portals, Mobile). 
   - For **EVERY** phase, create step-by-step, granular, atomic tasks.
   - Every single task **must** have: A Unique Task ID, a Highly Detailed Description, the Assigned Developer (DEV-1, 2, or 3), and Strict Dependencies (exact Task IDs that must be finished before this task can start).

---

## Deliverable 2: The Timeline & Tooling Strategy Document
Generate a separate, definitive guide covering project management and timeline estimation:

1. **Phase Estimation & Time Allocation**: 
   - Provide a realistic timeline (in weeks/months).
   - Recommend exactly what percentage of time should be spent on System Analysis vs. System Design vs. Active Implementation for each phase.
2. **Tooling Strategy & Justification**: 
   - **GitHub Issues**: Analyze and definitively answer if GitHub Issues is the best tool for this 3-developer remote team to track the granular tasks you generated. Explain how to use it properly.
   - **Docker**: Definitively answer whether Docker is necessary for this project. Explain the pros/cons of Dockerized Odoo vs. Bare-metal for a multi-tenant SaaS environment.
   - **Collaboration Stack**: Recommend the best tools for remote communication, CI/CD pipelines, and code review.

# Output Instructions
- Generate the documents as distinct, professional Markdown artifacts.
- Prioritize depth over brevity. I want a complete, exhaustive guide that leaves no ambiguity for the developers.
- Take your time. Ensure the task dependencies logically flow without blocking the team unnecessarily.
