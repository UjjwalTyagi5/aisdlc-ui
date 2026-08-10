"use client";

import { StageWorkbench } from "@/components/app/stage-workbench";

/**
 * Data Engineering agent — PRD §25.2 (Track 5 only).
 * Owner: Data Engineer. Discovery, profiling and optimisation are Safe;
 * registering a connector and deploying/scheduling the pipeline are
 * Consequential; accepting the pipeline as production-ready is the Sign-off.
 *
 * Identity nuance (§11): the Data Engineer links their own database/warehouse
 * account, so the agent's connector actions act as them — the same per-user
 * identity model as every other connector.
 */
export default function DataEngineeringPage() {
  return (
    <StageWorkbench
      phase="data_engineering"
      agent="data_engineering"
      title="Data Engineering"
      runLabel="Run Data Engineering agent"
      emptyTitle="No pipeline yet"
      emptyDescription="Connects read-only to source databases, warehouses and files; infers schema, volumetrics and key candidates; profiles data-quality signals. Then generates connector configuration (Snowflake, Redshift, data lake, JDBC/ODBC) and ELT/ETL pipeline code with incremental load, partitioning and a data-quality test scaffold — capturing lineage and per-field classification, plus performance and cost-optimization reports."
    />
  );
}
