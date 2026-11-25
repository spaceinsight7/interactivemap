# -*- coding: utf-8 -*-
"""
엑셀(특송기간 경로분석자료) + roadplus SHP  ->  웹 지도용 data/data.js

사용법 (저장소 최상위 폴더에서):
    pip install openpyxl pyshp numpy scipy
    python tools/build_data.py "<엑셀 파일>" "<roadplus_shapefile 폴더>"

엑셀 자료가 바뀌면 이 스크립트만 다시 돌리고 data/data.js 를 올리면 된다.

엑셀은 첫 번째 시트만 읽는다. 필요한 열:
    PATH_ID, ST_CITY_NM, ED_CITY_NM, SEQ_LINK, DSRC_ID, LENGTH, ETC, 방향(지방/서울),
    START_X, START_Y, END_X, END_Y, 0~23 의 "h_속도_km", "h_소요시간_분"

누적시간 산정
  - 지방방향(수도권 요금소 출발): 경로 SEQ 순서대로 소요시간을 더한다. 값 = 요금소에서 그 구간 끝까지.
  - 서울방향(수도권 요금소 도착): 경로 SEQ 역순으로 더한다. 값 = 그 구간 시작에서 요금소까지.
    (경로 첫 구간의 값이 가장 크다.)
  - 여러 경로가 같은 구간을 지나면 그중 최대값을 쓴다.

처리 순서
 1. SHP(GTM_H200_V3) 폴리곤 중심점을 이어 노선·방향별 중심선을 만든다.
 2. DSRC 구간을 경로 순서대로 같은 노선 중심선 위에 올린다.
    (엑셀 좌표가 틀린 구간은 앞뒤 정상 지점 사이를 구간 길이 비율로 나눠 채운다.)
 3. 구간 위에 약 1km 간격으로 점을 찍고 시간대별 누적시간을 나눠 준다.
 4. 격자 각 칸의 값 = 가까운 구간 점들의 (누적시간 + 직선거리 / OFF_SPEED) 거리 역가중 평균
    -> 방향별 24시간 x 격자. 고속도로 위에서는 엑셀 값과 같고, 멀어질수록 시간이 더해진다.
"""
import sys, os, json, math, base64, datetime, collections
import numpy as np
import openpyxl, shapefile
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import minimum_spanning_tree, connected_components
from scipy.ndimage import gaussian_filter

# ---------------------------------------------------------------- 설정값
GATES = ('서울', '서서울', '동서울', '남양주')   # 수도권 요금소(지방방향 출발지, 서울방향 도착지)
AGG = max                                     # 여러 경로가 겹치는 구간의 값: max(최대) 또는 min(최소)
SHP_NAME = 'GTM_H200_V3'               # 중심선을 뽑는 폴리곤
OFF_SPEED = 40.0       # 고속도로 밖 이동속도 가정 (km/h, 직선거리 기준)
CELL = 3000.0          # 격자 한 칸 (웹 메르카토르 m, 실제 약 2.4km)
IDW_POWER = 4.0        # 거리 역가중 지수 (클수록 가장 가까운 구간 값에 가까워진다)
IDW_EPS_KM = 0.5       # 거리 0 근처 보정
SMOOTH = 0.7           # 격자 평활 (칸 단위 가우시안 시그마)
SAMPLE_KM = 1.0        # 구간 위 표본점 간격
FIELD_MAX_KM = 110.0   # 분석 구간에서 이보다 먼 섬(제주 등)은 색을 칠하지 않는다
ON_CHAIN_M = 4500.0    # 엑셀 좌표가 중심선에서 이 거리 안이면 정상 좌표로 본다
VIEW_LONLAT = (125.55, 33.05, 130.05, 38.80)    # 화면에 그릴 범위 (서, 남, 동, 북)
FIELD_LONLAT = (125.95, 34.10, 129.70, 38.70)   # 격자를 계산할 범위 (본토)
# 지도에 시간을 적을 곳: (표시 이름, 경로의 ST_CITY_NM, ED_CITY_NM)
#   지방방향은 그 경로 마지막 구간의 값, 서울방향은 그 경로 첫 구간의 값
CITIES = {
    '지방': [('천안', '서울', '천안'), ('대전', '서울', '대전'), ('원주', '서울', '원주'),
            ('춘천', '남양주', '남춘천'), ('강릉', '서울', '강릉'), ('양양', '남양주', '양양'),
            ('대구', '서울', '대구'), ('울산', '서울', '울산'), ('부산', '서울', '대동'),
            ('광주', '서울', '광주'), ('목포', '서서울', '목포')],
    '서울': [('천안', '천안', '서울'), ('대전', '대전', '서울'), ('원주', '원주', '서울'),
            ('강릉', '강릉', '서울'), ('양양', '양양', '남양주'),
            ('대구', '대구', '서울'), ('울산', '울산', '서울'), ('부산', '대동', '서울'),
            ('광주', '광주', '서울'), ('목포', '목포', '서서울')],
}
R = 20037508.342789244
HERE = os.path.dirname(os.path.abspath(__file__))


def merc(lat, lon):
    return (lon * R / 180.0,
            math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0) * R / 180.0)


def kfac(y):
    """메르카토르 1m 가 실제 몇 m 인지 (위도에 따라 달라지는 축척)"""
    return 1.0 / np.cosh(np.asarray(y, float) / R * math.pi)


def _snap_box(lonlat, origin=None):
    x0, y0 = merc(lonlat[1], lonlat[0])
    x1, y1 = merc(lonlat[3], lonlat[2])
    if origin is None:
        return (x0, y1 - math.ceil((y1 - y0) / CELL) * CELL, x0 + math.ceil((x1 - x0) / CELL) * CELL, y1)
    ox, oy = origin                                    # 화면 범위의 왼쪽 위에 칸을 맞춘다
    fx0 = ox + round((x0 - ox) / CELL) * CELL
    fy1 = oy - round((oy - y1) / CELL) * CELL
    return (fx0, fy1 - round((y1 - y0) / CELL) * CELL, fx0 + round((x1 - x0) / CELL) * CELL, fy1)


BBOX = _snap_box(VIEW_LONLAT)
FIELD_BBOX = _snap_box(FIELD_LONLAT, (BBOX[0], BBOX[3]))


# ---------------------------------------------------------------- 1. 엑셀
def read_excel(path):
    """첫 번째 시트를 읽어 방향별 (구간, 경로) 를 만든다. 누적시간은 소요시간으로 다시 계산한다."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    it = wb.worksheets[0].iter_rows(values_only=True)
    col = {h: i for i, h in enumerate(next(it))}
    c_tt = [col['%d_소요시간_분' % h] for h in range(24)]
    c_sp = [col['%d_속도_km' % h] for h in range(24)]
    c_cum = [col.get('%d_누적시간_분' % h) for h in range(24)]
    raw = collections.OrderedDict()
    for r in it:
        if r[col['PATH_ID']] is None:
            continue
        key = (str(r[col['방향']]).strip(), r[col['PATH_ID']], str(r[col['ST_CITY_NM']]), str(r[col['ED_CITY_NM']]))
        raw.setdefault(key, []).append(r)
    dirs = {'지방': (collections.OrderedDict(), collections.OrderedDict()),
            '서울': (collections.OrderedDict(), collections.OrderedDict())}
    skipped, worst = [], 0.0
    for (d, pid, st, ed), rows in raw.items():
        # 지방방향은 수도권 요금소 출발, 서울방향은 수도권 요금소 도착 경로만 쓴다
        if d not in dirs or (d == '지방' and st not in GATES) or (d == '서울' and ed not in GATES):
            skipped.append('%s %s→%s (PATH %s, %d구간)' % (d, st, ed, pid, len(rows)))
            continue
        links, paths = dirs[d]
        rows.sort(key=lambda r: r[col['SEQ_LINK']])
        tt = np.array([[float(r[c]) for c in c_tt] for r in rows])           # 구간 x 24
        if d == '지방':
            cum = np.cumsum(tt, 0)                                           # SEQ 순서대로 누적
        else:
            cum = np.cumsum(tt[::-1], 0)[::-1]                               # SEQ 역순으로 누적
        if c_cum[0] is not None:                                             # 엑셀의 누적시간 열과 비교(확인용)
            sheet = np.array([[float(r[c]) for c in c_cum] for r in rows])
            worst = max(worst, float(np.abs(sheet - cum).max()))
        ids = []
        for n, r in enumerate(rows):
            i = r[col['DSRC_ID']]
            ids.append(i)
            L = links.get(i)
            if L is None:
                # 엑셀의 START_X/END_X 는 위도, START_Y/END_Y 는 경도
                L = links[i] = dict(
                    id=i, length=float(r[col['LENGTH']]),
                    a=np.array(merc(r[col['START_X']], r[col['START_Y']])),
                    b=np.array(merc(r[col['END_X']], r[col['END_Y']])),
                    tt=list(tt[n]), spd=[float(r[c]) for c in c_sp],
                    road=str(r[col['ETC']]).split('(')[0].strip(), T=list(cum[n]))
            else:
                L['T'] = [AGG(x, y) for x, y in zip(L['T'], cum[n])]
        paths[(pid, st, ed)] = ids
    return dirs, skipped, worst


# ---------------------------------------------------------------- 2. SHP 중심선
def tree_paths(P, dmax):
    """점들을 가까운 것끼리 이어(최소신장트리) 가장 긴 줄기부터 차례로 선으로 뽑는다."""
    n = len(P)
    if n < 2:
        return []
    pairs = cKDTree(P).query_pairs(dmax, output_type='ndarray')
    if len(pairs) == 0:
        return []
    w = np.hypot(*(P[pairs[:, 0]] - P[pairs[:, 1]]).T) + 1e-6
    mst = minimum_spanning_tree(coo_matrix((w, (pairs[:, 0], pairs[:, 1])), shape=(n, n))).tocoo()
    adj = collections.defaultdict(list)
    for i, j, d in zip(mst.row, mst.col, mst.data):
        adj[int(i)].append((int(j), float(d)))
        adj[int(j)].append((int(i), float(d)))

    def far(start, alive):
        dist, par, stack = {start: 0.0}, {start: None}, [start]
        while stack:
            u = stack.pop()
            for v, d in adj[u]:
                if v in alive and v not in dist:
                    dist[v], par[v] = dist[u] + d, u
                    stack.append(v)
        end = max(dist, key=dist.get)
        return end, dist, par

    out, alive = [], set(adj)
    while alive:
        s = next(iter(alive))
        a, _, _ = far(s, alive)
        b, dist, par = far(a, alive)
        path = [b]
        while par[path[-1]] is not None:
            path.append(par[path[-1]])
        comp = set(dist)
        if dist[b] > 2500 and len(path) >= 2:
            # 가지가 줄기에 붙는 자리의 점을 앞에 붙여 끊겨 보이지 않게 한다
            head = [v for v, _ in adj[path[-1]] if v not in alive]
            tail = [v for v, _ in adj[path[0]] if v not in alive]
            out.append(P[(tail[:1] + path + head[:1])])
            alive -= set(path)
        else:
            alive -= comp
    return out


def read_chains(shp_dir):
    rd = shapefile.Reader(os.path.join(shp_dir, SHP_NAME + '.shp'), encoding='cp949')
    groups = collections.defaultdict(list)
    for sr in rd.iterShapeRecords():
        key = sr.record[-1]                         # 예: 0010VDE00100 (노선 4자리 + VD + 방향)
        pts = np.asarray(sr.shape.points, float)
        x, y, x1, y1 = pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1]
        cr = x * y1 - x1 * y
        A = cr.sum() / 2.0
        c = pts.mean(0) if abs(A) < 1e-6 else \
            np.array([((x + x1) * cr).sum() / (6 * A), ((y + y1) * cr).sum() / (6 * A)])
        groups[(key[:4], key[6])].append(c)
    return {g: [Chain(c) for c in tree_paths(np.array(v), 11500.0)] for g, v in groups.items()}


class Chain:
    def __init__(self, pts):
        self.p = np.asarray(pts, float)
        self.s = np.concatenate([[0], np.cumsum(np.hypot(*(self.p[1:] - self.p[:-1]).T))])

    def project(self, q):
        a, ab = self.p[:-1], self.p[1:] - self.p[:-1]
        t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
        d = np.hypot(*(a + ab * t[:, None] - q).T)
        i = int(d.argmin())
        return float(self.s[i] + t[i] * (self.s[i + 1] - self.s[i])), float(d[i] * kfac(q[1]))

    def at(self, s):
        s = min(max(s, 0.0), self.s[-1])
        i = min(max(int(np.searchsorted(self.s, s, 'right')) - 1, 0), len(self.p) - 2)
        f = (s - self.s[i]) / max(self.s[i + 1] - self.s[i], 1e-9)
        return self.p[i] + (self.p[i + 1] - self.p[i]) * f

    def sub(self, s0, s1):
        lo, hi = min(s0, s1), max(s0, s1)
        pts = [self.at(lo)] + list(self.p[(self.s > lo) & (self.s < hi)]) + [self.at(hi)]
        return np.array(pts if s0 <= s1 else pts[::-1])


def place_run(run, links, chains, prev_exit):
    """같은 노선을 연달아 달리는 구간 묶음(run)을 중심선 위에 올린다. 구간별 (진입, 진출) 위치를 돌려준다."""
    route = run[0][:4]
    cands = [c for d in (run[0][6], 'S' if run[0][6] == 'E' else 'E') for c in chains.get((route, d), [])]
    best = None
    for ch in cands:
        pr = [(ch.project(links[i]['a']), ch.project(links[i]['b'])) for i in run]
        hit = sum((pa[1] < ON_CHAIN_M) + (pb[1] < ON_CHAIN_M) for pa, pb in pr)
        if best is None or hit > best[0]:
            best = (hit, ch, pr)
    if best is None or best[0] == 0:
        return None
    _, ch, pr = best
    n = len(run)
    mlen = np.array([links[i]['length'] / kfac(links[i]['a'][1]) for i in run])   # 메르카토르 길이
    A = [pa[0] if pa[1] < ON_CHAIN_M else None for pa, _ in pr]
    B = [pb[0] if pb[1] < ON_CHAIN_M else None for _, pb in pr]
    # 진행 방향 (중심선 누적거리가 늘어나는 쪽인지 줄어드는 쪽인지)
    mids = [(k, np.mean([v for v in (A[k], B[k]) if v is not None])) for k in range(n)
            if A[k] is not None or B[k] is not None]
    vote = sum(np.sign(m2 - m1) for x, (_, m1) in enumerate(mids) for _, m2 in mids[x + 1:])
    if vote == 0:
        k = mids[0][0]
        vote = (B[k] - A[k]) if (A[k] is not None and B[k] is not None) else 1.0
        if prev_exit is not None and A[k] is not None and B[k] is not None:
            if math.dist(links[run[k]]['b'], prev_exit) + 500 < math.dist(links[run[k]]['a'], prev_exit):
                vote = -vote
    sign = 1.0 if vote > 0 else -1.0
    # 시·종점이 진행 방향과 반대로 적힌 구간은 뒤집어 읽는다
    for k in range(n):
        if A[k] is not None and B[k] is not None and (B[k] - A[k]) * sign < 0 \
                and 0.4 * mlen[k] < abs(B[k] - A[k]) < 2.5 * mlen[k]:
            A[k], B[k] = B[k], A[k]
    # 구간 경계(node) 관측값: 앞 구간 끝과 뒤 구간 시작이 서로 맞으면 믿을 수 있는 기준점
    obs = [None] * (n + 1)
    for k in range(n + 1):
        e = B[k - 1] if k > 0 else None
        s = A[k] if k < n else None
        if e is not None and s is not None:
            if abs(e - s) < 1000:
                obs[k] = (e + s) / 2
        elif k == 0 and s is not None:
            obs[k] = s
        elif k == n and e is not None:
            obs[k] = e
    cum = np.concatenate([[0], np.cumsum(mlen)])
    idx = [k for k in range(n + 1) if obs[k] is not None]
    # 서로 모순 없는 기준점을 가장 많이 고른다 (순서와 구간 길이에 맞아야 함)
    bestlen, back = {}, {}
    for x, k in enumerate(idx):
        bestlen[k], back[k] = 1, None
        for j in idx[:x]:
            adv, want = (obs[k] - obs[j]) * sign, cum[k] - cum[j]
            if 0.5 * want - 300 < adv < 2.0 * want + 300 and bestlen[j] + 1 > bestlen[k]:
                bestlen[k], back[k] = bestlen[j] + 1, j
    if not idx:
        return None
    k = max(idx, key=lambda q: bestlen[q])
    anchors = []
    while k is not None:
        anchors.append(k)
        k = back[k]
    anchors.reverse()
    pos = np.zeros(n + 1)
    for k in anchors:
        pos[k] = obs[k]
    for j, k in zip(anchors[:-1], anchors[1:]):         # 기준점 사이는 길이 비율로 나눈다
        for m in range(j + 1, k):
            pos[m] = obs[j] + (obs[k] - obs[j]) * (cum[m] - cum[j]) / (cum[k] - cum[j])
    j, k = anchors[0], anchors[-1]
    for m in range(j):                                   # 기준점 바깥은 구간 길이만큼 이어 간다
        pos[m] = obs[j] - sign * (cum[j] - cum[m])
    for m in range(k + 1, n + 1):
        pos[m] = obs[k] + sign * (cum[m] - cum[k])
    lim = ch.s[-1]
    if k < n and not 0 <= pos[n] <= lim:                 # 중심선 끝을 넘으면 끝에 맞춰 줄인다
        edge = lim if sign > 0 else 0.0
        f = (edge - obs[k]) / (pos[n] - obs[k])
        pos[k + 1:] = obs[k] + (pos[k + 1:] - obs[k]) * f
    if j > 0 and not 0 <= pos[0] <= lim:
        edge = 0.0 if sign > 0 else lim
        f = (edge - obs[j]) / (pos[0] - obs[j])
        pos[:j] = obs[j] + (pos[:j] - obs[j]) * f
    return ch, pos, set(anchors)


def locate(links, paths, chains):
    stats = collections.Counter()
    for seq in paths.values():
        runs = []
        for i in seq:
            if runs and runs[-1][0][:4] == i[:4]:
                runs[-1].append(i)
            else:
                runs.append([i])
        prev_exit = None
        for run in runs:
            res = place_run(run, links, chains, prev_exit)
            for k, i in enumerate(run):
                L = links[i]
                if 'geom' not in L:
                    if res is not None and abs(res[1][k + 1] - res[1][k]) > 1:
                        L['geom'] = res[0].sub(res[1][k], res[1][k + 1])
                        if not (k in res[2] and k + 1 in res[2]):
                            L['fixed'] = True
                            stats['보정'] += 1
                        else:
                            stats['정상'] += 1
                    else:                                  # SHP 에 없는 구간: 엑셀 좌표를 직선으로
                        a, b = L['a'], L['b']
                        if prev_exit is not None and math.dist(b, prev_exit) + 500 < math.dist(a, prev_exit):
                            a, b = b, a
                        L['geom'] = np.array([a, b])
                        L['straight'] = True
                        stats['직선'] += 1
                prev_exit = L['geom'][-1]
    return stats


def drop_detached(links, paths, limit_km=30.0):
    """경로의 나머지와 limit_km 넘게 떨어져 놓인 구간(엑셀의 구간 번호·좌표 오류로 보이는 것)을 지도에서 뺀다."""
    keep, cand = set(), set()
    for seq in paths.values():
        parts = [[seq[0]]]
        for a, b in zip(seq[:-1], seq[1:]):
            ga, gb = links[a]['geom'][-1], links[b]['geom'][0]
            gap = math.dist(ga, gb) * float(kfac(ga[1])) / 1000.0
            if gap > limit_km:
                parts.append([])
            parts[-1].append(b)
        main = max(parts, key=len)
        keep.update(main)
        for part in parts:
            if part is not main:
                cand.update(part)
    gone = [i for i in links if i in cand and i not in keep]
    for i in gone:
        del links[i]
    return gone


# ---------------------------------------------------------------- 3. 표본점 · 격자
def sample_links(links, direction):
    P, T = [], []
    for L in links.values():
        g = L['geom']                                     # 진행 방향 순서(진입 -> 진출)
        seg = np.hypot(*(g[1:] - g[:-1]).T)
        s = np.concatenate([[0], np.cumsum(seg)])
        if s[-1] <= 0:
            continue
        m = max(1, int(round(s[-1] * kfac(g[0][1]) / (SAMPLE_KM * 1000))))
        f = np.concatenate([[0.0], (np.arange(m) + 1.0) / m])
        xy = np.stack([np.interp(f * s[-1], s, g[:, 0]), np.interp(f * s[-1], s, g[:, 1])], 1)
        val, tt = np.array(L['T']), np.array(L['tt'])
        if direction == '지방':                            # 값은 구간 끝 기준: 진입 = 값 - 소요시간
            t0, t1 = val - tt, val
        else:                                             # 값은 구간 시작 기준: 진출 = 값 - 소요시간
            t0, t1 = val, val - tt
        P.append(xy)
        T.append(t0[None, :] + f[:, None] * (t1 - t0)[None, :])
    return np.vstack(P), np.vstack(T)


def make_field(P, T):
    x0, y0, x1, y1 = FIELD_BBOX
    w, h = int(round((x1 - x0) / CELL)), int(round((y1 - y0) / CELL))
    cx = x0 + (np.arange(w) + 0.5) * CELL
    cy = y1 - (np.arange(h) + 0.5) * CELL                 # 위(북)에서 아래로
    out = np.zeros((24, h, w), np.float32)
    kp = kfac(P[:, 1])
    Tt = T.astype(np.float64)                              # 표본점 x 24
    for j in range(h):
        k = (kfac(cy[j]) + kp) / 2.0
        d = np.hypot(cx[:, None] - P[None, :, 0], cy[j] - P[None, :, 1]) * k[None, :] / 1000.0   # km
        wgt = 1.0 / (d + IDW_EPS_KM) ** IDW_POWER          # 가까운 구간일수록 크게 반영
        wgt /= wgt.sum(1, keepdims=True)
        # 값 = 가까운 구간들의 (누적시간 + 그 구간까지 직선거리 / OFF_SPEED) 가중평균
        out[:, j, :] = (wgt @ Tt + (wgt * d).sum(1, keepdims=True) / OFF_SPEED * 60.0).T
    if SMOOTH > 0:
        out = np.stack([gaussian_filter(g, SMOOTH, mode='nearest') for g in out])
    return out, w, h


# ---------------------------------------------------------------- 4. 내보내기
def simplify(pts, tol):
    """Douglas-Peucker"""
    pts = np.asarray(pts, float)
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        v = q - p
        seg = pts[a + 1:b] - p
        L2 = (v * v).sum()
        if L2 < 1e-9:
            d = np.hypot(*seg.T)
        else:
            t = np.clip((seg @ v) / L2, 0, 1)
            d = np.hypot(*(seg - t[:, None] * v).T)
        i = int(d.argmax())
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return pts[keep]


def to_world(pts):
    """메르카토르 -> 화면 좌표 (격자 칸 단위, 왼쪽 위가 원점)"""
    pts = np.asarray(pts, float)
    return np.stack([(pts[:, 0] - BBOX[0]) / CELL, (BBOX[3] - pts[:, 1]) / CELL], 1)


def flat(pts, nd=2):
    return [round(float(v), nd) for v in to_world(pts).ravel()]


def clip_rect(ring, box):
    x0, y0, x1, y1 = box
    for axis, val, ge in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        out = []
        for i in range(len(ring)):
            p, q = ring[i - 1], ring[i]
            pin = (p[axis] >= val) if ge else (p[axis] <= val)
            qin = (q[axis] >= val) if ge else (q[axis] <= val)
            if pin != qin:
                t = (val - p[axis]) / (q[axis] - p[axis])
                out.append(p + t * (q - p))
            if qin:
                out.append(q)
        ring = out
        if len(ring) < 3:
            return None
    return np.array(ring)


def read_land(P):
    fc = json.load(open(os.path.join(HERE, 'land.geojson'), encoding='utf-8'))
    tree = cKDTree(P)
    land = dict(field=[], kr=[], other=[])
    for f in fc['features']:
        for poly in f['geometry']['coordinates']:
            ring = np.array([merc(lat, lon) for lon, lat in poly[0][:-1]])
            ring = clip_rect(list(ring), BBOX)
            if ring is None:
                continue
            ring = simplify(np.vstack([ring, ring[:1]]), 350.0)
            if len(ring) < 4:
                continue
            if f['properties']['iso'] == 'KR':
                c = ring.mean(0)
                d = tree.query(c)[0] * float(kfac(c[1])) / 1000.0
                land['field' if d < FIELD_MAX_KM else 'kr'].append(flat(ring))
            else:
                land['other'].append(flat(ring))
    return land


def build_direction(d, links, paths, chains):
    stats = locate(links, paths, chains)
    gone = drop_detached(links, paths)
    if gone:
        print('[%s방향] 경로에서 30km 넘게 떨어져 지도에서 뺀 구간: %s' % (d, ', '.join(gone)))
    P, T = sample_links(links, d)
    field, w, h = make_field(P, T)
    print('[%s방향] 구간 %d개 / 경로 %d개 / 위치 %s / 표본점 %d개 / 값 범위 %.0f~%.0f분'
          % (d, len(links), len(paths), dict(stats), len(P), field.min(), field.max()))
    q = np.clip(np.round(field * 10), 0, 65535).astype('<u2')          # 0.1분 단위 16비트
    cities = []
    for name, st, ed in CITIES[d]:
        seq = next((s for (pid, a, b), s in paths.items() if a == st and b == ed), None)
        if not seq:
            print('  경로 없음:', name, st, ed)
            continue
        seq = [i for i in seq if i in links]
        L = links[seq[-1]] if d == '지방' else links[seq[0]]
        pt = L['geom'][-1:] if d == '지방' else L['geom'][:1]
        x, y = to_world(pt)[0]
        cities.append(dict(name=name, x=round(float(x), 2), y=round(float(y), 2), T=[round(v, 1) for v in L['T']]))
    gates = collections.OrderedDict()                                   # 수도권 요금소 위치
    for (pid, st, ed), seq in paths.items():
        name = st if d == '지방' else ed
        seq = [i for i in seq if i in links]
        if name not in gates and seq:
            g = links[seq[0]]['geom'][0] if d == '지방' else links[seq[-1]]['geom'][-1]
            x, y = to_world(g[None])[0]
            gates[name] = dict(name=name + 'TG', x=round(float(x), 2), y=round(float(y), 2))
    out = dict(
        grid=base64.b64encode(q.tobytes()).decode('ascii'),
        links=[dict(id=L['id'], road=L.get('road', ''), km=round(L['length'] / 1000.0, 1),
                    p=flat(simplify(L['geom'], 40.0)), T=[round(v, 1) for v in L['T']],
                    v=[int(round(v)) for v in L['spd']]) for L in links.values()],
        cities=cities, origins=list(gates.values()), paths=len(paths), dropped=gone)
    return out, P, w, h


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    xlsx, shp_dir = sys.argv[1], sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, '..', 'data', 'data.js')

    dirs, skipped, worst = read_excel(xlsx)
    for t in skipped:
        print('제외한 경로:', t)
    print('엑셀 누적시간 열과 다시 계산한 값의 최대 차이: %.3f분' % worst)
    chains = read_chains(shp_dir)
    out, pts = {}, []
    for d, key in (('지방', 'out'), ('서울', 'in')):
        links, paths = dirs[d]
        if not links:
            continue
        out[key], P, w, h = build_direction(d, links, paths, chains)
        pts.append(P)
    data = dict(
        meta=dict(source=os.path.basename(xlsx), built=datetime.date.today().isoformat(),
                  cell=CELL, bbox=BBOX, offSpeed=OFF_SPEED,
                  agg='최대' if AGG is max else '최소',
                  width=(BBOX[2] - BBOX[0]) / CELL, height=(BBOX[3] - BBOX[1]) / CELL,
                  kmPerUnit=round(CELL * float(kfac((BBOX[1] + BBOX[3]) / 2)) / 1000.0, 4)),
        grid=dict(w=w, h=h, x0=(FIELD_BBOX[0] - BBOX[0]) / CELL, y0=(BBOX[3] - FIELD_BBOX[3]) / CELL, scale=0.1),
        land=read_land(np.vstack(pts)),
        net=[flat(simplify(c.p, 60.0)) for cs in chains.values() for c in cs],
        dirs=out,
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('window.MAPDATA=' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print('저장: %s (%.1f MB)' % (os.path.abspath(out_path), os.path.getsize(out_path) / 1e6))
    return dirs, chains


if __name__ == '__main__':
    main()
