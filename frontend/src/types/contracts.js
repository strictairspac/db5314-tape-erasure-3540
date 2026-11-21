/** @typedef {{id:number, title:string, payload:object, author:string}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed', cutoff:string|null}} Run */
/** @typedef {{id:number, run_id:number, digest:string, report:{cutoff:string, summary:string, metrics:object, sections:Array}}} Evidence */
/** @typedef {{seeded:boolean, seeded_at:string|null, assets:number, holds:Array, archive:Array}} ReplicaState */
