import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta_classic as ta
import requests
import time

# [설정] 페이지 기본 설정
st.set_page_config(page_title="Master Trading Scanner v9.2", layout="wide")
st.title("⚡ 8-WAY 마스터 트레이딩 스캐너 v9.2 (미국 정규 증시 전용)")

# [보안] 텔레그램 설정 (secrets 암호/토큰 의존성 완전 제거)
TELEGRAM_TOKEN = ""
CHAT_ID = ""

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=3)
    except:
        pass

# [데이터 수집] OTC(장외주식) 차단 및 미국 3대 공식 증시 상장 종목만 엄격하게 필터링
@st.cache_data(ttl=86400)
def get_pure_us_tickers():
    # SEC 거래소(Exchange) 정보가 포함된 전용 API로 변경
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    headers = {'User-Agent': 'QuantScanner admin@quantscanner.com'}
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
    except Exception:
        st.error("⚠️ 미국 증시 데이터를 불러오지 못했습니다. 네트워크를 확인하세요.")
        return {}

    exclude_keywords = ['ETF', 'FUND', 'TRUST', 'SPAC', 'PROSHARES', 'VANGUARD', 'ISHARES', 'INVESCO', 'DIREXION']
    
    # [수정 1] 미국 주요 공식 상장 거래소만 허용 (OTC, Pink Sheet 등 장외주식 원천 차단)
    allowed_exchanges = ['Nasdaq', 'NYSE', 'NYSE AMEX', 'NYSE Arca', 'Cboe BZX']
    
    stock_dict = {}
    
    # SEC 데이터 포맷 추출 (data['data'] 배열 순회)
    for item in data.get('data', []):
        ticker = item[2].replace('.', '-')
        title = item[1].upper()
        exchange = item[3]
        
        # 거래소가 허용된 정규 미국 증시가 아니면 패스
        if exchange not in allowed_exchanges:
            continue
        
        # 비정상 티커 필터링
        if len(ticker) > 5 or not ticker.replace('-', '').isalpha():
            continue
        if any(keyword in title.split() for keyword in exclude_keywords):
            continue
            
        stock_dict[ticker] = item[1]
        
    return stock_dict

# [전략 엔진] 에러 방어 및 정확한 지표 인덱싱
def evaluate_strategy(ticker, ticker_name, df, mode, params):
    try:
        # 데이터가 최소 60개(60일선 등)가 안되면 전략 계산 불가
        if len(df) < 60: return None
        
        c = df.iloc[-1]
        p = df.iloc[-2]
        
        # 보조 지표 계산
        rsi_s = ta.rsi(df['Close'], length=14)
        bb_s = ta.bbands(df['Close'], length=20, std=2)
        macd_s = ta.macd(df['Close'])
        
        # 지표 계산 실패 시 패스 (에러 원천 차단)
        if rsi_s is None or bb_s is None or macd_s is None or len(macd_s.columns) < 3: 
            return None
        
        rsi = rsi_s.iloc[-1]
        bb_lower = bb_s.iloc[:, 0].iloc[-1]
        bb_upper = bb_s.iloc[:, 2].iloc[-1]
        
        # MACD 히스토그램 연산
        macd_hist_col = macd_s.columns[1]
        macd_hist_prev = macd_s[macd_hist_col].iloc[-2]
        macd_hist_curr = macd_s[macd_hist_col].iloc[-1]
        
        vol_avg = df['Volume'].iloc[-20:].mean()
        sma20 = ta.sma(df['Close'], 20).iloc[-1]
        sma60 = ta.sma(df['Close'], 60).iloc[-1]
        
        match = False
        score = 0
        
        # 전략 논리
        if mode.startswith("1"):
            match = (rsi < params['rsi_limit']) and (c['Close'] > c['Open']) and \
                    (macd_hist_curr > macd_hist_prev) and (c['Volume'] > vol_avg * params['vol_mult'])
        elif mode.startswith("2"): 
            match = (rsi < params['rsi_limit']) and (c['Close'] < bb_lower)
        elif mode.startswith("3"): 
            match = (c['Close'] > bb_upper) and (c['Volume'] > vol_avg * params['vol_mult'])
        elif mode.startswith("4"): 
            price_change = ((c['Close'] - p['Close']) / p['Close']) * 100
            match = (c['Volume'] > vol_avg * params['vol_mult']) and (price_change >= params['price_limit'])
        elif mode.startswith("5"): 
            rolling_max = df['High'].rolling(20).max().iloc[-1]
            match = (c['Close'] >= rolling_max * 0.98) and (c['Close'] > sma20 > sma60)
        elif mode.startswith("6"): 
            match = (c['Open'] < p['Close'] * 0.98) and (c['Close'] > c['Open']) and (c['Close'] > p['Low'])
        elif mode.startswith("7"):
            prev_sma20 = ta.sma(df['Close'], 20).iloc[-2]
            prev_sma60 = ta.sma(df['Close'], 60).iloc[-2]
            match = (prev_sma20 <= prev_sma60) and (sma20 > sma60) and (rsi < 70)
        elif mode.startswith("8"):
            price_change = ((c['Close'] - p['Close']) / p['Close']) * 100
            vol_ratio = c['Volume'] / vol_avg if vol_avg > 0 else 0
            score = (vol_ratio * 0.4) + (price_change * 0.4) + (rsi / 100 * 0.2)
            match = score > params['score_limit']

        if match:
            # [수정 2] 현재가(Close)를 미국 시장 정확도에 맞춰 엄격하게 반환
            return {"Ticker": ticker, "종목명": ticker_name, "현재가": round(c['Close'], 2), 
                    "변동률(%)": round(((c['Close']-p['Close'])/p['Close'])*100, 2),
                    "스코어": round(score, 2), "전략": mode.split(".")[0]}
    except Exception:
        return None
    return None

# [UI 및 프리셋 로직]
STRATEGIES = [
    "1. 정밀 바닥 탈출 (RSI+MACD+볼륨)", "2. 극한 낙폭 과대 (볼린저하단 이탈)", 
    "3. 볼린저 상단 돌파", "4. 거래량 폭발 (섹터강세)", 
    "5. 신고가 추세 (정배열)", "6. 갭 메우기 반등", 
    "7. 중장기 스윙 (골든크로스)", "8. 강력한 수급 스코어링"
]

if 'strategy' not in st.session_state:
    st.session_state.update({'strategy': STRATEGIES[0], 'rsi_limit': 40, 'vol_mult': 1.2, 'price_limit': 3.0, 'score_limit': 3.0})

def apply_presets():
    mode = st.session_state.strategy
    if mode.startswith("1"): st.session_state.update({'rsi_limit': 45, 'vol_mult': 1.1}) 
    elif mode.startswith("2"): st.session_state.rsi_limit = 35
    elif mode.startswith("3"): st.session_state.vol_mult = 2.0
    elif mode.startswith("4"): st.session_state.update({'vol_mult': 2.5, 'price_limit': 4.0})
    elif mode.startswith("8"): st.session_state.score_limit = 4.0

with st.sidebar:
    st.header("⚙️ 스캐너 튜닝")
    mode = st.selectbox("전략 선택", STRATEGIES, key='strategy', on_change=apply_presets)
    st.markdown("---")
    
    params = {
        'rsi_limit': st.slider("RSI 상한선 (바닥 잡기용)", 10, 60, key='rsi_limit'),
        'vol_mult': st.slider("거래량 폭발 배수", 1.0, 10.0, key='vol_mult', step=0.1),
        'price_limit': st.slider("최소 상승률 (%)", 1.0, 20.0, key='price_limit', step=0.5),
        'score_limit': st.slider("수급 스코어 컷라인", 1.0, 10.0, key='score_limit', step=0.5)
    }
    
    st.markdown("---")
    interval = st.selectbox("시간봉", ["15m", "30m", "1h", "1d", "1wk"], index=3)
    limit = st.slider("검색 종목 범위 (시총 상위 순)", 100, 10000, 2000, step=100)

if st.button("🚀 안정형 고속 스캔 시작", type="primary"):
    pure_us_stocks = get_pure_us_tickers()
    if not pure_us_stocks: st.stop()
        
    target_tickers = list(pure_us_stocks.keys())[:limit]
    
    # 야후 파이낸스 분봉 한계 처리
    if interval in ["15m", "30m"]: period = "60d"
    elif interval == "1h": period = "730d"
    elif interval == "1wk": period = "5y"
    else: period = "2y"
    
    results = []
    
    # [핵심] Rate Limit 방지를 위해 배치 사이즈를 150으로 하향 조정 유지 (v9.1 동일)
    batch_size = 150 
    progress_bar = st.progress(0, text="데이터 수집 시작...")
    
    for i in range(0, len(target_tickers), batch_size):
        batch = target_tickers[i:i+batch_size]
        progress_text = f"다운로드 및 분석 중... ({min(i+batch_size, limit)} / {limit} 종목)"
        progress_bar.progress((i + len(batch)) / limit, text=progress_text)
        
        try:
            # yfinance 다운로드 실행 (야후 네이티브 스레드 사용)
            data = yf.download(batch, period=period, interval=interval, group_by='ticker', threads=True, progress=False)
        except Exception:
            time.sleep(2) # 튕겼을 경우 휴식 시간 부여
            continue
            
        if data.empty:
            continue
            
        # 데이터프레임 병합 및 예외 처리 로직
        if isinstance(data.columns, pd.MultiIndex):
            available_tickers = data.columns.levels[0]
            for ticker in batch:
                if ticker in available_tickers:
                    df = data[ticker].dropna()
                    res = evaluate_strategy(ticker, pure_us_stocks.get(ticker, ticker), df, mode, params)
                    if res: results.append(res)
        else:
            if len(batch) == 1: 
                df = data.dropna()
                res = evaluate_strategy(batch[0], pure_us_stocks.get(batch[0], batch[0]), df, mode, params)
                if res: results.append(res)
                
        # 야후 서버 디도스 감지 회피용 강제 쿨다운 (필수)
        time.sleep(1.5) 

    progress_bar.progress(1.0, text="스캔 완료!")
    st.success(f"✔️ 총 {len(target_tickers)}개 종목 스캔 완료! (포착: {len(results)}건)")
    
    if results:
        result_df = pd.DataFrame(results)
        sort_col = "스코어" if mode.startswith("8") else "변동률(%)"
        
        # Streamlit Deprecation Warning 해결 (width='stretch' 사용)
        st.dataframe(
            result_df.sort_values(by=sort_col, ascending=False), 
            width='stretch', 
            hide_index=True
        )
    else:
        st.warning("⚠️ 현재 설정하신 조건에 일치하는 종목이 없습니다. (사이드바에서 RSI 상한선을 높이거나 거래량 배수를 낮춰보세요)")