import pandas as pd
import yfinance as yf
from pykrx import stock
import requests
import json
import time
import sys
from datetime import datetime, timedelta

# 1. 분석 기간 설정 (최근 5년)
end_date = datetime.today().strftime("%Y%m%d")
start_date = (datetime.today() - timedelta(days=365*5)).strftime("%Y%m%d")

print("데이터 수집을 시작합니다...")

# 2. KOSPI 지수 수집 (pykrx 실패 시 yfinance ^KS11 백업 로직 추가)
max_retries = 3
kospi = pd.DataFrame()

print("[1단계] pykrx를 통한 KOSPI 데이터 수집 시도...")
for attempt in range(max_retries):
    try:
        temp_kospi = stock.get_index_ohlcv(start_date, end_date, "1001", name_display=False)
        if not temp_kospi.empty:
            kospi = temp_kospi[['시가', '종가']].rename(columns={'시가': 'KOSPI_Open', '종가': 'KOSPI_Close'})
            print(f"✅ KOSPI 데이터 수집 완료 (pykrx 시도 횟수: {attempt + 1})")
            break
        else:
            print(f"⚠️ 데이터가 비어있습니다. 재시도 중... ({attempt + 1}/{max_retries})")
    except Exception as e:
        print(f"⚠️ pykrx 수집 에러: {e}. 재시도 중... ({attempt + 1}/{max_retries})")
    time.sleep(3)

# pykrx 수집 실패 시 Yahoo Finance에서 백업 수집
if kospi.empty:
    print("\n⚠️ pykrx 수집 실패로 인해 [백업] yfinance(^KS11)로 KOSPI 수집을 시도합니다...")
    try:
        yf_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:]
        yf_end = end_date[:4] + '-' + end_date[4:6] + '-' + end_date[6:]
        # auto_adjust=False 추가로 경고 메시지 제거
        temp_kospi = yf.download('^KS11', start=yf_start, end=yf_end, auto_adjust=False)
        
        if isinstance(temp_kospi.columns, pd.MultiIndex):
            temp_kospi.columns = temp_kospi.columns.get_level_values(0)
            
        if not temp_kospi.empty:
            kospi = temp_kospi[['Open', 'Close']].rename(columns={'Open': 'KOSPI_Open', 'Close': 'KOSPI_Close'})
            kospi.index = kospi.index.tz_localize(None)
            print("✅ KOSPI 데이터 수집 완료 (yfinance 백업 사용)")
    except Exception as e:
        print(f"❌ KOSPI 백업 데이터 수집 실패: {e}")

if kospi.empty:
    print("❌ KOSPI 데이터 수집에 완전히 실패했습니다. 연산을 중단합니다.")
    sys.exit()

# 3. NASDAQ 지수 수집 (^IXIC)
print("\n[2단계] NASDAQ 데이터 수집 시도...")
try:
    # auto_adjust=False 추가로 경고 메시지 제거
    nasdaq = yf.download('^IXIC', start=start_date[:4]+'-'+start_date[4:6]+'-'+start_date[6:], 
                         end=end_date[:4]+'-'+end_date[4:6]+'-'+end_date[6:], auto_adjust=False)
    
    if isinstance(nasdaq.columns, pd.MultiIndex):
        nasdaq.columns = nasdaq.columns.get_level_values(0)
    
    nasdaq = nasdaq[['Close']].rename(columns={'Close': 'NDX_Close'})
    nasdaq.index = nasdaq.index.tz_localize(None)
    print("✅ NASDAQ 데이터 수집 완료")
except Exception as e:
    print(f"❌ NASDAQ 데이터 수집 실패: {e}")
    sys.exit()

# 4. 데이터 병합 및 시차(Lag) 정렬
df = pd.merge(kospi, nasdaq, left_index=True, right_index=True, how='left')

df['NDX_Close_prev'] = df['NDX_Close'].shift(1)
df['NDX_Return'] = (df['NDX_Close_prev'] - df['NDX_Close'].shift(2)) / df['NDX_Close'].shift(2)

df['KOSPI_Prev_Close'] = df['KOSPI_Close'].shift(1)
df['KOSPI_Daily_Return'] = (df['KOSPI_Close'] - df['KOSPI_Prev_Close']) / df['KOSPI_Prev_Close']
df['Gap_Return'] = (df['KOSPI_Open'] - df['KOSPI_Prev_Close']) / df['KOSPI_Prev_Close']
df['Intraday_Return'] = (df['KOSPI_Close'] - df['KOSPI_Open']) / df['KOSPI_Open']

df = df.dropna()

# 5. 조건부 확률 도출 및 예상 등락률 산출
cond_gap_up = (df['NDX_Return'] >= 0.01) & (df['Gap_Return'] > 0)
cond_intraday_up = df['Intraday_Return'] > 0

total_events = len(df[cond_gap_up])
success_events = len(df[cond_gap_up & cond_intraday_up])
prob = (success_events / total_events) * 100 if total_events > 0 else 0

latest_ndx_return = (df['NDX_Return'].iloc[-1] * 100)

# 베타(Beta) 계수 산출 및 코스피 예상 등락률 계산
beta = df['KOSPI_Daily_Return'].cov(df['NDX_Return']) / df['NDX_Return'].var()
expected_kospi_return = latest_ndx_return * beta

report_message = f"""
분석 기준일: {datetime.today().strftime('%Y-%m-%d')}

조건 충족 횟수: {total_events}일
갭상승 후 장중 양봉 마감 횟수: {success_events}일
추세 유지 확률: {prob:.2f}%

전날 나스닥 등락률 : {latest_ndx_return:.2f}%
코스피 예상 등락률 : {expected_kospi_return:.2f}%
"""

print("\n--- 분석 결과 ---")
print(report_message)

# 6. 구글 앱스 스크립트 웹훅으로 전송
webhook_url = "https://script.google.com/macros/s/AKfycbxDeelSjMDB1dD-Lsj2nRs8azQUb0TwDyGcSjCTHvtUqfHYbGb-q_pTB-PPyPvXZ-63kg/exec"

payload = {
    "date": datetime.today().strftime("%Y-%m-%d"),
    "ndx_return": f"{latest_ndx_return:.2f}%",
    "prob": f"{prob:.2f}%",
    "message": report_message
}

try:
    if "복사한_구글_웹앱_URL" not in webhook_url:
        response = requests.post(
            webhook_url, 
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        if response.status_code == 200:
            print("\n✅ 구글 시트 기록 및 이메일 전송 요청 완료")
        else:
            print(f"\n⚠️ 웹훅 전송 실패 (상태 코드: {response.status_code})")
    else:
        print("\nℹ️ 웹훅 URL이 기본값이어서 전송을 생략했습니다.")
except Exception as e:
    print(f"\n웹훅 전송 중 에러 발생: {e}")
