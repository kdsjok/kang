"""
품질등급 ML 모델 분석기
- RandomForest, XGBoost, GradientBoosting, LightGBM, SVM 모델 지원
- 카테고리별 최적 모델 자동 선택
- Hidden Risk Data 추출
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.svm import SVC

from I_ACE.config.settings import get_settings


@dataclass
class MLAnalysisResult:
    """ML 분석 결과"""
    success: bool
    high_risk_df: pd.DataFrame = None
    summary: pd.DataFrame = None
    error_message: str = ""


class QualityMLAnalyzer:
    """
    품질등급 ML 분석기
    - 파일 경로 또는 DataFrame을 입력받아 ML 분석 실행
    - 카테고리별 최적 모델 자동 선택
    - Hidden Risk 데이터 추출
    """
    
    def __init__(self, data: Any = None, settings=None):
        """
        초기화
        
        Args:
            data: 데이터 파일 경로 또는 DataFrame
            settings: 설정 객체
        """
        self.settings = settings or get_settings()
        
        # 데이터 로드
        if data is not None:
            if isinstance(data, pd.DataFrame):
                self.df = data.copy()
            elif isinstance(data, str):
                self.df = pd.read_excel(data)
            else:
                raise ValueError("data는 DataFrame 또는 파일 경로여야 합니다.")
        else:
            self.df = None
        
        # 모델 학습 함수 매핑
        self.model_trainers = {
            'RandomForest': self._train_random_forest,
            'XGBoost': self._train_xgboost,
            'GradientBoosting': self._train_gradient_boost,
            'LightGBM': self._train_lightgbm,
            'SVM': self._train_svm
        }
        
        # 설정에서 Feature 로드
        self.features = self.settings.ML_FEATURES
        self.category_mapping = self.settings.ML_CATEGORY_MAPPING
        self.risk_threshold = self.settings.ML_RISK_THRESHOLD
        self.precision_threshold = self.settings.ML_PRECISION_THRESHOLD
    
    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        데이터 전처리 (결측치, 이상치, 중복제거)
        
        Args:
            df: 원본 데이터프레임
        
        Returns:
            전처리된 데이터프레임
        """
        df = df.fillna(0)
        
        # 이상치 제거 (검사수량=0 또는 입고수량=0)
        df = df[~((df['TOTAL_INSP_QTY'] == 0) | (df['TOTAL_GR_QTY'] == 0))]
        
        # 중복 제거
        cols_to_check = ['PART_CD', 'TOTAL_LOT_CNT', 'TOTAL_GR_QTY', 'TOTAL_INSP_QTY']
        df = df.drop_duplicates(subset=cols_to_check, keep='first').copy()
        
        return df
    
    def _get_category_data(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """
        카테고리별 데이터 분리
        
        Args:
            df: 전처리된 데이터프레임
        
        Returns:
            카테고리별 데이터 딕셔너리
        """
        processed_df = self._preprocess_data(df)
        
        categories = {}
        
        for cat_key, cat_values in self.category_mapping.items():
            cat_df = processed_df[processed_df['CATEGORY_L'].isin(cat_values)]
            
            if len(cat_df) > 0:
                # VEND_RISK_SCORE 추가
                risk_score = cat_df.groupby('MAKER_CD')['TOTAL_INSP_PPM'].mean()
                cat_df['VEND_RISK_SCORE'] = cat_df['MAKER_CD'].map(risk_score).fillna(0)
                
                # TARGET 추가 (1,2,3등급=0, 4,5등급=1)
                cat_df['TARGET_1'] = cat_df['PART_Q_LEVEL'].apply(
                    lambda x: 0 if x in [1, 2, 3] else 1
                )
            
            categories[cat_key] = {
                'df': cat_df,
                'name': '+'.join(cat_values)
            }
        
        return categories
    
    def _train_random_forest(self, X, y, df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
        """RandomForest 모델 학습"""
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        model = RandomForestClassifier(
            n_estimators=100, max_depth=6, class_weight='balanced', random_state=42
        )
        model.fit(X_train, y_train)
        
        y_pred = model.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        
        # 전체 데이터에 대한 예측
        df = df.copy()
        df['AI_Risk_Score'] = model.predict_proba(X)[:, 1]
        df['Rule_Decision'] = df['TARGET_1'].apply(lambda x: 'Rule: Safe' if x == 0 else 'Rule: Risky')
        df['AI_Decision'] = df['AI_Risk_Score'].apply(
            lambda x: f'AI: Risky (>{int(self.risk_threshold*100)}%)' if x >= self.risk_threshold else 'AI: Safe'
        )
        
        # Hidden Risk 추출 (Rule은 안전한데 AI는 위험으로 판정)
        result_df = df[
            (df['AI_Decision'].str.contains('Risky')) & 
            (df['Rule_Decision'] == 'Rule: Safe')
        ].copy()
        result_df = result_df.drop(
            columns=['VEND_RISK_SCORE', 'TARGET_1', 'AI_Risk_Score', 'Rule_Decision', 'AI_Decision'],
            errors='ignore'
        )
        
        return precision, recall, result_df
    
    def _train_xgboost(self, X, y, df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
        """XGBoost 모델 학습"""
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        ratio = float(np.sum(y_train == 0)) / np.sum(y_train == 1) if np.sum(y_train == 1) > 0 else 1
        
        model = XGBClassifier(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            scale_pos_weight=ratio, random_state=42, eval_metric='logloss'
        )
        model.fit(X_train, y_train)
        
        y_pred = model.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        
        df = df.copy()
        df['AI_Risk_Score'] = model.predict_proba(X)[:, 1]
        df['Rule_Decision'] = df['TARGET_1'].apply(lambda x: 'Rule: Safe' if x == 0 else 'Rule: Risky')
        df['AI_Decision'] = df['AI_Risk_Score'].apply(
            lambda x: f'AI: Risky (>{int(self.risk_threshold*100)}%)' if x >= self.risk_threshold else 'AI: Safe'
        )
        
        result_df = df[
            (df['AI_Decision'].str.contains('Risky')) & 
            (df['Rule_Decision'] == 'Rule: Safe')
        ].copy()
        result_df = result_df.drop(
            columns=['VEND_RISK_SCORE', 'TARGET_1', 'AI_Risk_Score', 'Rule_Decision', 'AI_Decision'],
            errors='ignore'
        )
        
        return precision, recall, result_df
    
    def _train_gradient_boost(self, X, y, df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
        """GradientBoosting 모델 학습"""
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        model = GradientBoostingClassifier(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            subsample=0.8, random_state=42
        )
        model.fit(X_train, y_train)
        
        y_pred = model.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        
        df = df.copy()
        df['AI_Risk_Score'] = model.predict_proba(X)[:, 1]
        df['Rule_Decision'] = df['TARGET_1'].apply(lambda x: 'Rule: Safe' if x == 0 else 'Rule: Risky')
        df['AI_Decision'] = df['AI_Risk_Score'].apply(
            lambda x: f'AI: Risky (>{int(self.risk_threshold*100)}%)' if x >= self.risk_threshold else 'AI: Safe'
        )
        
        result_df = df[
            (df['AI_Decision'].str.contains('Risky')) & 
            (df['Rule_Decision'] == 'Rule: Safe')
        ].copy()
        result_df = result_df.drop(
            columns=['VEND_RISK_SCORE', 'TARGET_1', 'AI_Risk_Score', 'Rule_Decision', 'AI_Decision'],
            errors='ignore'
        )
        
        return precision, recall, result_df
    
    def _train_lightgbm(self, X, y, df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
        """LightGBM 모델 학습"""
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        ratio = float(np.sum(y_train == 0)) / np.sum(y_train == 1) if np.sum(y_train == 1) > 0 else 1
        
        model = LGBMClassifier(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            scale_pos_weight=ratio, subsample=0.8, random_state=42,
            num_leaves=50, verbose=-1
        )
        model.fit(X_train, y_train)
        
        y_pred = model.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        
        df = df.copy()
        df['AI_Risk_Score'] = model.predict_proba(X)[:, 1]
        df['Rule_Decision'] = df['TARGET_1'].apply(lambda x: 'Rule: Safe' if x == 0 else 'Rule: Risky')
        df['AI_Decision'] = df['AI_Risk_Score'].apply(
            lambda x: f'AI: Risky (>{int(self.risk_threshold*100)}%)' if x >= self.risk_threshold else 'AI: Safe'
        )
        
        result_df = df[
            (df['AI_Decision'].str.contains('Risky')) & 
            (df['Rule_Decision'] == 'Rule: Safe')
        ].copy()
        result_df = result_df.drop(
            columns=['VEND_RISK_SCORE', 'TARGET_1', 'AI_Risk_Score', 'Rule_Decision', 'AI_Decision'],
            errors='ignore'
        )
        
        return precision, recall, result_df
    
    def _train_svm(self, X, y, df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
        """SVM 모델 학습"""
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        model = SVC(
            C=1.0, kernel='rbf', gamma='scale', class_weight='balanced',
            probability=True, random_state=42
        )
        model.fit(X_train, y_train)
        
        y_pred = model.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        
        df = df.copy()
        df['AI_Risk_Score'] = model.predict_proba(X)[:, 1]
        df['Rule_Decision'] = df['TARGET_1'].apply(lambda x: 'Rule: Safe' if x == 0 else 'Rule: Risky')
        df['AI_Decision'] = df['AI_Risk_Score'].apply(
            lambda x: f'AI: Risky (>{int(self.risk_threshold*100)}%)' if x >= self.risk_threshold else 'AI: Safe'
        )
        
        result_df = df[
            (df['AI_Decision'].str.contains('Risky')) & 
            (df['Rule_Decision'] == 'Rule: Safe')
        ].copy()
        result_df = result_df.drop(
            columns=['VEND_RISK_SCORE', 'TARGET_1', 'AI_Risk_Score', 'Rule_Decision', 'AI_Decision'],
            errors='ignore'
        )
        
        return precision, recall, result_df
    
    def _select_best_model(
        self, 
        df: pd.DataFrame, 
        features: List[str]
    ) -> Tuple[Optional[str], Optional[Dict]]:
        """
        최적 모델 선택
        
        Args:
            df: 학습할 데이터프레임
            features: Feature 컬럼 리스트
        
        Returns:
            (최적 모델명, 결과 딕셔너리)
        """
        X = df[features]
        y = df['TARGET_1']
        
        # 모든 모델 학습
        model_results = {}
        for model_name, train_func in self.model_trainers.items():
            try:
                precision, recall, result_df = train_func(X, y, df.copy())
                model_results[model_name] = {
                    'precision': precision,
                    'recall': recall,
                    'df': result_df
                }
            except Exception as e:
                continue
        
        # precision > 임계값인 모델만 필터링
        filtered_models = {
            name: result for name, result in model_results.items() 
            if result['precision'] > self.precision_threshold
        }
        
        if not filtered_models:
            return None, None
        
        # recall이 가장 높은 모델 선택
        best_model_name = max(filtered_models, key=lambda x: filtered_models[x]['recall'])
        return best_model_name, filtered_models[best_model_name]
    
    def run_analysis(self, df: pd.DataFrame = None, output_path: str = None) -> MLAnalysisResult:
        """
        전체 ML 분석 실행
        
        Args:
            df: 분석할 데이터프레임 (None이면 초기화 시 데이터 사용)
            output_path: 결과 저장 경로
        
        Returns:
            MLAnalysisResult: 분석 결과
        """
        df = df if df is not None else self.df
        
        if df is None:
            return MLAnalysisResult(
                success=False,
                error_message="분석할 데이터가 없습니다."
            )
        
        print('='*60)
        print('ML 모델 분석 시작')
        print('='*60)
        
        categories = self._get_category_data(df)
        
        all_results = []
        summary_data = []
        
        for cat_key, cat_info in categories.items():
            cat_df = cat_info['df']
            features = self.features.get(cat_key, [])
            
            if len(cat_df) == 0 or len(features) == 0:
                summary_data.append({
                    'CATEGORY': cat_info['name'],
                    'BEST_MODEL': 'None',
                    'PRECISION': 0,
                    'RECALL': 0,
                    'DATA_COUNT': 0
                })
                continue
            
            best_model_name, best_result = self._select_best_model(cat_df, features)
            
            if best_result is not None:
                all_results.append(best_result['df'])
                summary_data.append({
                    'CATEGORY': cat_info['name'],
                    'BEST_MODEL': best_model_name,
                    'PRECISION': round(best_result['precision'], 4),
                    'RECALL': round(best_result['recall'], 4),
                    'DATA_COUNT': len(best_result['df'])
                })
            else:
                summary_data.append({
                    'CATEGORY': cat_info['name'],
                    'BEST_MODEL': 'None',
                    'PRECISION': 0,
                    'RECALL': 0,
                    'DATA_COUNT': 0
                })
        
        # 결과 출력
        print('\n[ML 모델 분석 결과 요약]')
        summary_df = pd.DataFrame(summary_data)
        print(summary_df.to_string(index=False))
        
        # 결과 결합
        if all_results:
            final_result = pd.concat(all_results, ignore_index=True)
            print(f'\n총 추출 데이터 수: {len(final_result)}개')
        else:
            final_result = pd.DataFrame()
            print('\n⚠️ 저장할 결과 데이터가 없습니다.')
        
        # 저장
        if output_path and len(final_result) > 0:
            final_result.to_excel(output_path, index=False)
            print(f'✅ 결과 저장 완료: {output_path}')
        
        return MLAnalysisResult(
            success=True,
            high_risk_df=final_result,
            summary=summary_df
        )
    
    def get_high_risk_items(self, df: pd.DataFrame = None) -> pd.DataFrame:
        """
        ML High Risk 항목만 반환
        
        Args:
            df: 분석할 데이터프레임
        
        Returns:
            ML High Risk 항목 데이터프레임
        """
        result = self.run_analysis(df)
        return result.high_risk_df if result.success else pd.DataFrame()