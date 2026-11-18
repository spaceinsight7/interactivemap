# 시간대별 고속도로 소요시간 지도

특송기간 지방방향 DSRC 통행시간 자료로 만든 웹 지도입니다.
수도권 요금소(서울·서서울·동서울·남양주TG)에서 전국 각지까지 걸리는 시간을 30분 간격 색 띠와 1시간 간격 흰 선으로 보여 주고, 0시부터 23시까지 30초에 한 바퀴 돌며 바뀝니다.

## 폴더 구성

| 파일 | 내용 |
|---|---|
| `index.html` | 화면 |
| `css/style.css` | 모양 |
| `js/app.js` | 지도 그리기, 재생, 확대·이동, 설명 상자 |
| `data/data.js` | 지도 자료 (엑셀·SHP에서 만든 결과물) |
| `vendor/d3.min.js` | 등치선 계산과 확대·이동에 쓰는 d3 라이브러리 |
| `tools/build_data.py` | 엑셀·SHP → `data/data.js` 변환 스크립트 |
| `tools/land.geojson` | 해안선 (Natural Earth 1:10m) |

서버 없이 `index.html`을 더블클릭해도 열립니다.

## GitHub Pages에 올리기

1. GitHub에서 새 저장소를 만듭니다. (무료 계정은 공개 저장소여야 Pages를 쓸 수 있습니다.)
2. 이 폴더에서 아래 명령을 실행합니다.

   ```
   git init
   git add .
   git commit -m "소요시간 지도"
   git branch -M main
   git remote add origin https://github.com/<계정>/<저장소>.git
   git push -u origin main
   ```

3. 저장소의 Settings → Pages에서 Source를 `Deploy from a branch`, Branch를 `main` / `(root)`로 정하고 저장합니다.
4. 1~2분 뒤 `https://<계정>.github.io/<저장소>/` 에서 열립니다.

## 자료가 바뀌었을 때

엑셀 파일을 새로 받으면 변환 스크립트만 다시 돌리고 `data/data.js`를 올리면 됩니다.

```
pip install openpyxl pyshp numpy scipy
python tools/build_data.py "<엑셀 파일 경로>" "<roadplus_shapefile 폴더 경로>"
```

엑셀은 지금 파일과 같은 구성이어야 합니다.

- `중복제거 DSRC 통행시간` 시트: DSRC_ID, LENGTH, 시·종점 좌표, 0시~23시 누적 통행시간
- `지방방향 누적소요시간` 시트: 경로별 구간 순서 (PATH_ID, SEQ_LINK, DSRC_ID)
- `기준점` 시트: 출발 요금소
- 첫 번째 시트: 구간별 속도·소요시간 (설명 상자의 구간 속도, 구간 진입 시각 계산에 사용)

## 계산 방법

1. SHP(`GTM_H200_V3`) 폴리곤의 중심점을 이어 노선·방향별 중심선을 만듭니다.
2. DSRC 구간을 경로 순서대로 같은 노선 중심선 위에 올립니다. 엑셀 좌표가 중심선에서 벗어난 구간은 앞뒤 정상 지점 사이를 구간 길이 비율로 나눠 채웁니다. (현재 468개 중 55개)
3. 구간 위에 약 1km 간격으로 점을 찍고 누적 통행시간을 나눠 줍니다.
4. 지도의 각 지점 값은 가까운 구간 점들의 `누적시간 + 직선거리 ÷ 시속 40km`를 거리 역가중 평균한 값입니다. 고속도로 위는 엑셀 값과 같고, 고속도로에서 멀어질수록 시간이 더해집니다.
5. 화면에서는 이 격자(약 2.4km 간격, 24시간분)를 앞뒤 시간대로 이어 가며 등치선을 그립니다.

## 바꿀 수 있는 값

`tools/build_data.py` 위쪽

- `OFF_SPEED` 고속도로 밖 이동속도 가정 (기본 시속 40km)
- `CELL` 격자 크기. 줄이면 선이 더 매끄럽고 `data.js`가 커집니다.
- `CITIES` 지도에 시간을 적는 도착지

`js/app.js` 위쪽

- `LOOP_SEC` 24시간을 보여 주는 시간 (기본 30초)
- `STEP`, `MAX_MIN` 색 띠 간격(30분)과 색이 끝나는 시간(10시간)
- `STAT_CITIES` 아래 표에 나오는 도착지

## 알아 둘 점

- 엑셀 값이 경로 중 최대값이라, 우회 경로가 합쳐지는 곳(대전IC 앞 등)은 바로 다음 구간보다 시간이 길게 나옵니다.
- 서울양양선 동홍천~양양 구간 등 엑셀 좌표가 틀린 곳은 SHP 노선 위에 구간 길이대로 다시 놓았습니다.
- 제주 등 분석 구간에서 110km 넘게 떨어진 섬은 색을 칠하지 않습니다.
- 글꼴(Pretendard)은 jsDelivr CDN에서 불러옵니다. 인터넷이 안 되면 기본 글꼴(맑은 고딕 등)로 보입니다.
- 지도 색은 `js/app.js` 위쪽의 색 상수와 색 띠(`ramp`), 화면 색은 `css/style.css` 맨 위 `:root`에서 바꿉니다.
