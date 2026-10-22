import pandas as pd
import yfinance as yf
from pykrx import stock
import requests
import json
import time
import sys
from datetime import datetime, timedelta

# 1. 분석 기간 및 한국 시간(KST) 설정
kst_now = datetime.utcnow() + timedelta(hours=9)
end_date = kst_now.strftime("%Y%m%d")
start_date = (kst_now - timedelta(days=365*5)).strftime("%Y%m%d")
recent_60_days = (kst_now - timedelta(days=90)).strftime("%Y%m%d")

print("데이터 수집을 시작합니다...")

# 2. KOSPI 지수 수집
max_retries = 3
kospi = pd.DataFrame()

print("[1단계] KOSPI 데이터 수집 시도...")
for attempt in range(max_retries):
    try:
        temp_kospi = stock.get_index_ohlcv(start_date, end_date, "1001", name_display=False)
        if not temp_kospi.empty:
            kospi = temp_kospi[['시가', '종가']].rename(columns={'시가': 'KOSPI_Open', '종가': 'KOSPI_Close'})
            print("KOSPI 데이터 수집 완료 (pykrx)")
            break
    except Exception as e:
        time.sleep(3)

if kospi.empty:
    try:
        yf_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:]
        yf_end = end_date[:4] + '-' + end_date[4:6] + '-' + end_date[6:]
        temp_kospi = yf.download('^KS11', start=yf_start, end=yf_end, auto_adjust=False)
        if isinstance(temp_kospi.columns, pd.MultiIndex):
            temp_kospi.columns = temp_kospi.columns.get_level_values(0)
        if not temp_kospi.empty:
            kospi = temp_kospi[['Open', 'Close']].rename(columns={'Open': 'KOSPI_Open', 'Close': 'KOSPI_Close'})
            kospi.index = kospi.index.tz_localize(None)
            print("KOSPI 데이터 수집 완료 (yfinance 백업)")
    except Exception as e:
        print("KOSPI 수집 실패")
        sys.exit()

kospi = kospi.dropna()
recent_kospi_high = kospi['KOSPI_Close'].tail(60).max()
recent_kospi_low = kospi['KOSPI_Close'].tail(60).min()
current_kospi = kospi['KOSPI_Close'].iloc[-1]

# 3. 미국 지수 및 한국 야간 대체지표 통합 수집
print("[2단계] 미국 지수 및 한국 야간 지표(EWY) 수집...")
night_futures_msg = "데이터 수집 실패"
try:
    yf_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:]
    yf_end = end_date[:4] + '-' + end_date[4:6] + '-' + end_date[6:]
    
    us_markets = yf.download(['^IXIC', '^SOX', 'EWY'], start=yf_start, end=yf_end, auto_adjust=False)
    if isinstance(us_markets.columns, pd.MultiIndex):
        us_close = us_markets['Close']
    else:
        us_close = us_markets
        
    nasdaq = us_close[['^IXIC']].rename(columns={'^IXIC': 'NDX_Close'}).dropna()
    sox = us_close[['^SOX']].rename(columns={'^SOX': 'SOX_Close'}).dropna()
    ewy = us_close[['EWY']].rename(columns={'EWY': 'EWY_Close'}).dropna()
    
    nasdaq.index = nasdaq.index.tz_localize(None)
    sox.index = sox.index.tz_localize(None)
    ewy.index = ewy.index.tz_localize(None)
    
    latest_ndx_return = ((nasdaq['NDX_Close'].iloc[-1] - nasdaq['NDX_Close'].iloc[-2]) / nasdaq['NDX_Close'].iloc[-2]) * 100
    latest_sox_return = ((sox['SOX_Close'].iloc[-1] - sox['SOX_Close'].iloc[-2]) / sox['SOX_Close'].iloc[-2]) * 100
    
    last_ewy = ewy['EWY_Close'].iloc[-1]
    prev_ewy = ewy['EWY_Close'].iloc[-2]
    ewy_return = ((last_ewy - prev_ewy) / prev_ewy) * 100
    
    direction = "상승" if ewy_return > 0 else "하락" if ewy_return < 0 else "보합"
    night_futures_msg = f"종가 ${last_ewy:,.2f} ({direction} {ewy_return:+.2f}%)"
    
    print("미국 지수 및 한국 야간 지표 수집 완료")
except Exception as e:
    print(f"미국 지수 수집 실패: {e}")
    sys.exit()

# 4. 코스피 수급 데이터 수집
print("[3단계] 코스피 수급 데이터 수집...")
investor_msg = "해외 IP 차단으로 인한 데이터 수집 생략"
try:
    b_days = stock.get_business_days_dates(recent_60_days, end_date)
    last_b_day = b_days[-1].strftime("%Y%m%d") if len(b_days) > 0 else end_date
    
    investor_df = stock.get_market_trading_value_by_investor(last_b_day, last_b_day, "KOSPI")
    net_buy_col = '순매수' if '순매수' in investor_df.columns else investor_df.columns[-1]
    
    retail_net = investor_df.loc['개인', net_buy_col] / 100000000
    foreign_net = investor_df.loc['외국인', net_buy_col] / 100000000
    inst_net = investor_df.loc['기관합계', net_buy_col] / 100000000
    
    investor_msg = f"외국인: {foreign_net:,.0f}억 / 기관: {inst_net:,.0f}억 / 개인: {retail_net:,.0f}억"
    print("수급 데이터 수집 완료")
except Exception as e:
    print("수급 데이터 수집 실패 (기본값 처리)")

# 5. 데이터 병합 및 통계 확률 연산
df = pd.merge(kospi, nasdaq, left_index=True, right_index=True, how='left')

df['NDX_Close_prev'] = df['NDX_Close'].shift(1)
df['NDX_Return'] = (df['NDX_Close_prev'] - df['NDX_Close'].shift(2)) / df['NDX_Close'].shift(2)
df['KOSPI_Prev_Close'] = df['KOSPI_Close'].shift(1)
df['KOSPI_Daily_Return'] = (df['KOSPI_Close'] - df['KOSPI_Prev_Close']) / df['KOSPI_Prev_Close']
df['Gap_Return'] = (df['KOSPI_Open'] - df['KOSPI_Prev_Close']) / df['KOSPI_Prev_Close']
df['Intraday_Return'] = (df['KOSPI_Close'] - df['KOSPI_Open']) / df['KOSPI_Open']

df = df.dropna()

cond_gap_up = (df['NDX_Return'] >= 0.01) & (df['Gap_Return'] > 0)
cond_intraday_up = df['Intraday_Return'] > 0

total_events = len(df[cond_gap_up])
success_events = len(df[cond_gap_up & cond_intraday_up])
prob = (success_events / total_events) * 100 if total_events > 0 else 0

beta = df['KOSPI_Daily_Return'].cov(df['NDX_Return']) / df['NDX_Return'].var()
expected_kospi_return = latest_ndx_return * beta

# 6. 엘리어트 파동 및 피보나치 레벨 연산
wave_diff = recent_kospi_high - recent_kospi_low
fib_236 = recent_kospi_high - (wave_diff * 0.236)
fib_382 = recent_kospi_high - (wave_diff * 0.382)
fib_500 = recent_kospi_high - (wave_diff * 0.500)
fib_618 = recent_kospi_high - (wave_diff * 0.618)

if current_kospi > fib_500:
    trend_msg = "상승 추세 (상승 5파 진행 또는 눌림목 구간)"
else:
    trend_msg = "하락 조정 추세 (하락 3파 또는 조정 국면)"

# 7. 등락률에 따른 색상 결정 헬퍼 함수
def get_color(val):
    if val > 0:
        return "#e83e8c" # 상승(빨강계열)
    elif val < 0:
        return "#007bff" # 하락(파랑계열)
    else:
        return "#333333" # 보합(검정계열)

ndx_color = get_color(latest_ndx_return)
sox_color = get_color(latest_sox_return)
kospi_exp_color = get_color(expected_kospi_return)
ewy_color = get_color(ewy_return)

# 8. 블로그 포스팅용 예쁜 HTML 템플릿 생성 (이모지 및 특수기호 완전 제거)
html_report = f"""
<div style="font-family: 'Malgun Gothic', sans-serif; max-width: 800px; margin: 0 auto; color: #333;">
  
  <p style="text-align: center; color: #777; font-size: 14px;">분석 기준일: {kst_now.strftime('%Y년 %m월 %d일')}</p>

  <h2 style="border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50;">글로벌 시장 변동 (전일장 마감 기준)</h2>
  <table style="width: 100%; border-collapse: collapse; text-align: center; margin-bottom: 20px; font-size: 16px;">
    <tr style="background-color: #f8f9fa; border-top: 2px solid #ddd; border-bottom: 1px solid #ddd;">
      <th style="padding: 12px;">지수명</th>
      <th style="padding: 12px;">등락률</th>
    </tr>
    <tr style="border-bottom: 1px solid #eee;">
      <td style="padding: 10px; font-weight: bold;">미국 나스닥 (NASDAQ)</td>
      <td style="padding: 10px; font-weight: bold; color: {ndx_color};">{latest_ndx_return:+.2f}%</td>
    </tr>
    <tr style="border-bottom: 1px solid #eee;">
      <td style="padding: 10px; font-weight: bold;">필라델피아 반도체 (SOX)</td>
      <td style="padding: 10px; font-weight: bold; color: {sox_color};">{latest_sox_return:+.2f}%</td>
    </tr>
    <tr style="border-bottom: 2px solid #ddd;">
      <td style="padding: 10px; font-weight: bold;">KOSPI 야간 대체지표 (MSCI 한국 ETF)</td>
      <td style="padding: 10px; font-weight: bold; color: {ewy_color};">{ewy_return:+.2f}% (종가 ${last_ewy:,.2f})</td>
    </tr>
  </table>

  <h2 style="border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50;">파동 분석 및 수급 동향</h2>
  <div style="background-color: #f1f8ff; border-left: 4px solid #007bff; padding: 15px; margin-bottom: 20px;">
    <p><strong>수급 동향 (순매수):</strong> {investor_msg}</p>
    <p><strong>현재 파동 위치:</strong> {trend_msg} (코스피 현재가: {current_kospi:,.2f})</p>
    <ul style="line-height: 1.8;">
      <li><strong>상승 시 돌파 목표 (23.6%):</strong> <span style="color: #e83e8c; font-weight: bold;">{fib_236:,.2f}</span></li>
      <li><strong>하락 시 1차 지지선 (38.2%):</strong> <span style="color: #007bff; font-weight: bold;">{fib_382:,.2f}</span></li>
      <li><strong>하락 시 2차 지지선 (61.8%):</strong> <span style="color: #007bff; font-weight: bold;">{fib_618:,.2f}</span></li>
    </ul>
  </div>

  <h2 style="border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50;">오늘 장 퀀트 봇 통계 전망</h2>
  <div style="border: 1px solid #ddd; padding: 20px; text-align: center; border-radius: 8px; background-color: #fff;">
    <h3 style="margin-top: 0; color: #555;">코스피 통계적 예상 등락률</h3>
    <p style="font-size: 32px; font-weight: bold; margin: 10px 0; color: {kospi_exp_color};">{expected_kospi_return:+.2f}%</p>
    <hr style="border: 0; border-top: 1px dashed #ccc; margin: 20px 0;">
    <h3 style="margin-top: 0; color: #555;">미국장 상승 연동 코스피 장중 양봉 유지 확률</h3>
    <p style="font-size: 28px; font-weight: bold; margin: 10px 0; color: #ff9900;">{prob:.2f}%</p>
    <p style="font-size: 13px; color: #888;">(최근 5년 데이터 기준: 조건 충족 횟수 {total_events}일 중 {success_events}일 양봉 마감)</p>
  </div>
  
  <p style="font-size: 12px; color: #999; text-align: right; margin-top: 30px;">※ 본 리포트는 자동화된 퀀트 봇에 의해 작성되었으며, 투자의 참고 자료로만 활용하시기 바랍니다.</p>
</div>
"""

# 9. 메일용 텍스트 리포트 생성 (이모지 및 특수기호 완전 제거)
report_message = f"""
[종합 주식시장 시차 동조화 및 파동 분석 리포트]

분석 기준일: {kst_now.strftime('%Y-%m-%d')}

- 글로벌 시장 변동 (전일장 기준)
- KOSPI 야간 대체지표 (MSCI 한국 ETF): {night_futures_msg}
- 미국 나스닥 지수: {latest_ndx_return:+.2f}%
- 필라델피아 반도체: {latest_sox_return:+.2f}%

- 코스피 최근 영업일 수급 동향 (순매수)
- {investor_msg}

- 엘리어트 파동 & 피보나치 분석 (최근 60영업일)
- 코스피 현재가: {current_kospi:,.2f}
- 현재 파동 위치: {trend_msg}

- 오늘 장 통계적 전망 (최근 5년 데이터 기준)
- 코스피 통계적 예상 등락률: {expected_kospi_return:+.2f}%
- 미국장 상승 연동 코스피 장중 양봉 유지 확률: {prob:.2f}%
"""

print("\n--- 분석 결과 ---")
print(report_message)

# 10. 구글 앱스 스크립트 웹훅 URL (신규 적용 완료)
webhook_url = "https://script.google.com/macros/s/AKfycbxsrM75Ecuk70Xsf3jgMTsvCQiJpyYT6Q-tsQhIGeHq5J7opkB77-892jHdDtAuQdf6dA/exec"

payload = {
    "date": kst_now.strftime("%Y-%m-%d"),
    "ndx_return": f"{latest_ndx_return:.2f}%",
    "prob": f"{prob:.2f}%",
    "message": html_report
}

try:
    if "복사한_구글_웹앱_URL" not in webhook_url:
        response = requests.post(
            webhook_url, 
            headers={"Content-Type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            timeout=10
        )
        if response.status_code == 200:
            print("\n구글 시트 기록, 이메일 전송, 블로그 포스팅 요청 완료")
        else:
            print(f"\n웹훅 전송 실패 (상태 코드: {response.status_code})")
    else:
        print("\n웹훅 URL이 기본값이어서 전송을 생략했습니다.")
except Exception as e:
    print(f"\n웹훅 전송 중 에러 발생: {e}")
