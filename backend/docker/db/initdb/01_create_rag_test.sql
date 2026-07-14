-- 初回起動(空ボリューム)時のみ実行される。POSTGRES_DB(rag_dev)に加えテスト専用DBを作成する。
-- 開発/テストDB分離の背景・運用方針は docs/architecture.md §9 / docs/decisions.md 参照。
CREATE DATABASE rag_test;
