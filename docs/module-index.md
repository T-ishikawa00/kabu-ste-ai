# 全実装ファイル索引

保持した91ファイルを領域別に案内します。全ファイルの動作状態は未検証です。静的未接続は廃止・不要・未実装を意味しません。旧方式/試作の判断は出自や呼出し証拠が揃うまで保留し、明示的な欠落は制約に記録します。

## バックテスト・検証

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [backtest_rules.py](../backtest_rules.py) | 独立・利用未確認 | Trade L42, calc_rci L52, prepare_data L69 |
| [run_backtest.py](../run_backtest.py) | 独立・利用未確認 | load_data_files L36, run L51 |
| [run_backtest_hybrid.py](../run_backtest_hybrid.py) | 独立・利用未確認 | load_data_files L37, run L61 |
| [step4_backtest.py](../step4_backtest.py) | 独立・利用未確認 | backtest L14 |
| [step4_process_daily_log.py](../step4_process_daily_log.py) | 独立・利用未確認 | find_live_data_files L25, process_file L30, main L173 |
| [step5_backtest.py](../step5_backtest.py) | 独立・利用未確認 | load_data_files L34, run L42 |
| [step5_debug_xray.py](../step5_debug_xray.py) | 独立・利用未確認 | normalize_column_names L17, xray_scan L42 |
| [step5_optimize_rules.py](../step5_optimize_rules.py) | 独立・利用未確認 | normalize_column_names L29, load_data L52, simulate_strategy L76 |
| [verify_v22.py](../verify_v22.py) | 独立・利用未確認 | load_config_values L28, run_verification L46 |

## 取得・データ加工・特徴量

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [build_dataset_from_logs.py](../build_dataset_from_logs.py) | 独立・利用未確認 | parse_args L15, main L21 |
| [check_7011.py](../check_7011.py) | 独立・利用未確認 | script / HTML / BAT |
| [check_data.py](../check_data.py) | 独立・利用未確認 | check L19 |
| [check_files.py](../check_files.py) | 独立・利用未確認 | script / HTML / BAT |
| [debug_check_data.py](../debug_check_data.py) | 独立・利用未確認 | check_api_response L18, check_saved_files L62, check_screening_sample L94 |
| [debug_jquants.py](../debug_jquants.py) | 独立・利用未確認 | test_connection L7 |
| [features.py](../features.py) | 独立・利用未確認 | _norm L4, entry_feature_vector L9, exit_feature_vector L22 |
| [fetch_ohlcv_yahoo.py](../fetch_ohlcv_yahoo.py) | 独立・利用未確認 | script / HTML / BAT |
| [fix_datetime_format.py](../fix_datetime_format.py) | 独立・利用未確認 | fix_file L21, main L56 |
| [market_temperature.py](../market_temperature.py) | 独立・利用未確認 | MarketTemperature L4 |
| [step0_backfill_data.py](../step0_backfill_data.py) | 独立・利用未確認 | get_target_codes L25, main L47 |
| [step0_smart_fetch.py](../step0_smart_fetch.py) | 独立・利用未確認 | get_headers L32, fetch_smart L35, save_to_csv L81 |
| [step0_sniper_fill.py](../step0_sniper_fill.py) | 独立・利用未確認 | get_headers L32, fetch_smart L35, save_to_csv L81 |
| [step0_yfinance_patch.py](../step0_yfinance_patch.py) | 独立・利用未確認 | get_target_codes L32, download_and_save L41, main L99 |
| [step1_backfill_fast_v2.py](../step1_backfill_fast_v2.py) | 独立・利用未確認 | get_target_codes L25, main L47 |
| [step1_backfill_fast_v4.py](../step1_backfill_fast_v4.py) | 独立・利用未確認 | get_id_token L32, main L41 |
| [step1_jquants_daily.py](../step1_jquants_daily.py) | BATからの参照あり | get_headers L28, convert_code L31, is_valid_code L42 |
| [step1_prepare_data.py](../step1_prepare_data.py) | 独立・利用未確認 | process_file L37, main L165 |
| [step1_setup_data.py](../step1_setup_data.py) | 独立・利用未確認 | download_and_save L34, main L88 |
| [step_raw_to_merged.py](../step_raw_to_merged.py) | 独立・利用未確認 | calc_rci_series L31, safe_get_column L54, process_raw_file L65 |
| [tool_generate_training_data.py](../tool_generate_training_data.py) | 独立・利用未確認 | calc_rci_series L28, process_daily_file L49, main L120 |
| [tool_normalize_data_raw.py](../tool_normalize_data_raw.py) | 独立・利用未確認 | get_line_count L21, main L29 |
| [tool_select_to_raw.py](../tool_select_to_raw.py) | 独立・利用未確認 | get_line_count L25, main L33 |
| [utils_common.py](../utils_common.py) | main入口から静的に接続 | now L26, jst_iso L30, ensure_dir L34 |
| [utils_compile_raw.py](../utils_compile_raw.py) | 独立・利用未確認 | normalize_columns L25, main L51 |
| [utils_import_archive.py](../utils_import_archive.py) | 独立・利用未確認 | main L22 |

## 強化学習・モデル・方策

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [bandit_linucb.py](../bandit_linucb.py) | 独立・利用未確認 | LinUCB L4 |
| [migrate_models_71_to_73_safe.py](../migrate_models_71_to_73_safe.py) | 独立・利用未確認 | _atomic_write L14, _try_load L21, main L37 |
| [model_registry.py](../model_registry.py) | main入口から静的に接続 | ModelRegistry L13 |
| [offline_train_theta.py](../offline_train_theta.py) | 独立・利用未確認 | parse_args L6, load_dataset L14, score_subset L27 |
| [step2_env.py](../step2_env.py) | 独立・利用未確認 | TradingEnv L22 |
| [step2_env_universal.py](../step2_env_universal.py) | 独立・利用未確認 | TradingEnv L16 |
| [step3_train.py](../step3_train.py) | 独立・利用未確認 | DetailedStatsCallback L81, train L104 |
| [step3_train_individual.py](../step3_train_individual.py) | 独立・利用未確認 | DetailedStatsCallback L81, train L104 |
| [train_from_ohlcv.py](../train_from_ohlcv.py) | 独立・利用未確認 | _fallback_tick L18, load_csv L38, simulate_symbol L56 |
| [train_individual.py](../train_individual.py) | 独立・利用未確認 | DetailedStatsCallback L73, train L121 |
| [train_policy_online.py](../train_policy_online.py) | 独立・利用未確認 | _load_model L35, _save_model L72, _clip L80 |
| [train_universal.py](../train_universal.py) | 独立・利用未確認 | UniversalTradingEnv L55, WinRateCallback L86, load_all_data L148 |

## 戦略・リスク・証券API・注文

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [broker.py](../broker.py) | main入口から静的に接続 | SymbolLock L21, Broker L60 |
| [entry_decider.py](../entry_decider.py) | 独立・利用未確認 | EntryDecider L17 |
| [exit_decider.py](../exit_decider.py) | 独立・利用未確認 | ExitDecider L16 |
| [kabus_api.py](../kabus_api.py) | main入口から静的に接続 | KabusAPI L22 |
| [regime_manager.py](../regime_manager.py) | 独立・利用未確認 | RegimeManager L4 |
| [risk_sizer.py](../risk_sizer.py) | 独立・利用未確認 | RiskSizer L21 |
| [strategy_router.py](../strategy_router.py) | main入口から静的に接続 | Context L15, StrategyRouter L57 |
| [symbol_gate.py](../symbol_gate.py) | 独立・利用未確認 | SymbolGate L14 |

## 統合・WebSocket・市場イベント制御

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [controller.py](../controller.py) | main入口から静的に接続 | setup_logger L67, calc_rci_series L94, _hhmm L115 |
| [controller_ws.py](../controller_ws.py) | main入口から静的に接続 | WebsocketController L36 |
| [main.py](../main.py) | main入口から静的に接続 | setup_logging L21, main L32 |

## 記録・監視・集計・既存UI

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [analyze_details.py](../analyze_details.py) | 独立・利用未確認 | main L7 |
| [analyze_log_text.py](../analyze_log_text.py) | 独立・利用未確認 | main L7 |
| [analyze_logs.py](../analyze_logs.py) | 独立・利用未確認 | ensure_dir L22, load_latest_log L26, read_file_safe L46 |
| [check_log.py](../check_log.py) | 独立・利用未確認 | script / HTML / BAT |
| [dashboard.html](../dashboard.html) | 独立・利用未確認 | script / HTML / BAT |
| [data_collector.py](../data_collector.py) | main入口から静的に接続 | DataCollector L15 |
| [kpi_rollup.py](../kpi_rollup.py) | 独立・利用未確認 | _iter_trades L5, rollup L12 |
| [notifier.py](../notifier.py) | main入口から静的に接続 | Notifier L16 |
| [short_term_memory.py](../short_term_memory.py) | 独立・利用未確認 | ShortTermMemory L6 |
| [telemetry.py](../telemetry.py) | main入口から静的に接続 | Telemetry L20 |
| [yesterday_report_complete.py](../yesterday_report_complete.py) | 独立・利用未確認 | ensure_dir L14, load_yesterday_log L18, read_file_safely L39 |

## 運用・設定・補助・試作の独立入口

| 実ソース | 静的な位置付け | 主な定義（抜粋） |
| --- | --- | --- |
| [auth_login.py](../auth_login.py) | 独立・利用未確認 | get_gmail_service L27, find_latest_otp L42, main L67 |
| [auto_login.py](../auto_login.py) | BATからの参照あり | getaddrinfo_ipv4 L17, get_gmail_service L44, find_latest_otp L59 |
| [check_context.py](../check_context.py) | 独立・利用未確認 | script / HTML / BAT |
| [check_gmail.py](../check_gmail.py) | 独立・利用未確認 | main L12 |
| [check_screening.py](../check_screening.py) | 独立・利用未確認 | check_stock L16 |
| [config_loader.py](../config_loader.py) | main入口から静的に接続 | SymbolCfg L20, PortfolioCfg L31, TradingCfg L35 |
| [daily_mission.py](../daily_mission.py) | 独立・利用未確認 | wait_until_tomorrow_830 L6, main L34 |
| [find_url.py](../find_url.py) | 独立・利用未確認 | find_correct_url L6 |
| [get_pos.py](../get_pos.py) | 独立・利用未確認 | script / HTML / BAT |
| [launch_multi_bot.py](../launch_multi_bot.py) | 独立・利用未確認 | main L32 |
| [morning_batch.bat](../morning_batch.bat) | 独立・利用未確認 | script / HTML / BAT |
| [night_batch.bat](../night_batch.bat) | 独立・利用未確認 | script / HTML / BAT |
| [start_daily.bat](../start_daily.bat) | 独立・利用未確認 | script / HTML / BAT |
| [step2_screening.py](../step2_screening.py) | BATからの参照あり | load_config_lists L46, convert_code L74, is_valid_code L80 |
| [step6_inject_config.py](../step6_inject_config.py) | BATからの参照あり | load_yaml_data L44, rescue_passwords L52, load_screening_results L65 |
| [step7_morning_filter.py](../step7_morning_filter.py) | BATからの参照あり | KabuAPI L44, analyze_board L77, load_candidates L134 |
| [step99_emergency_exit.py](../step99_emergency_exit.py) | 独立・利用未確認 | ConfigWrapper L6, main L17 |
| [test_notify.py](../test_notify.py) | 独立・利用未確認 | MockConfig L6 |
| [test_run.py](../test_run.py) | 独立・利用未確認 | test_sequence L9 |
| [wait_840.py](../wait_840.py) | BATからの参照あり | script / HTML / BAT |
| [wait_time.py](../wait_time.py) | BATからの参照あり | wait_until_target_time L4 |
| [wakeup.py](../wakeup.py) | BATからの参照あり | script / HTML / BAT |

全定義位置・import情報は[module-catalog.json](module-catalog.json)、byte/ASTの保全は[source-map.json](source-map.json)を参照してください。
