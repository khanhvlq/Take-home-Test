from __future__ import annotations

from abc import ABC, abstractmethod

from airflow.sdk import DAG

from core.config import BaseDagConfig


class BaseDagBuilder(ABC):
    def __init__(self, config: BaseDagConfig) -> None:
        self.config = config

    def build(self) -> DAG:
        dag = DAG(
            dag_id=self.config.dag_id,
            start_date=self.config.start_date,
            schedule=self.config.schedule,
            catchup=self.config.catchup,
            default_args=self.config.default_args,
            max_active_runs=self.config.max_active_runs,
            max_active_tasks=self.config.max_active_tasks,
            tags=self.config.tags,
        )
        self.register_tasks(dag)
        return dag

    @abstractmethod
    def register_tasks(self, dag: DAG) -> None:
        raise NotImplementedError
