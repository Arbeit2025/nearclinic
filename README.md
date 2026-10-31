# nearclinic（近所の病院・クリニック検索）

地名・駅名を入力すると、その周辺にある病院・クリニック・診療所を距離順に一覧表示する、単一ファイル Flask の小さな Web ツールです。

## できること

- 地名・駅名（例：八王子駅）から場所を特定し、半径 1km / 2km / 3km / 5km の範囲を検索
- OpenStreetMap に登録されている医療機関（amenity=hospital / clinic / doctors）を表示
- 各施設について、名前・種別・住所・距離・診療時間（登録がある場合）・電話番号（登録がある場合）を表示
- 結果は距離の近い順。データベースは使わず、検索結果は保存しません

## ⚠️ 注意点（大事です）

- このツールの情報は OpenStreetMap の登録データに基づきます。**診療科・診療時間・休診日・待ち時間などの情報は、登録されていないことも多く、実際と異なる場合があります**
- **受診の前に、必ず公式サイトや電話で確認してください**
- **緊急時は 119 番**へ連絡してください
- 網羅性も保証できません。「近くに病院があるのに表示されない」こともあります

## データ出典

- 地図データ：© [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- ジオコーディング（地名→緯度経度）：[Nominatim](https://nominatim.openstreetmap.org/)
- 施設検索：[Overpass API](https://overpass-api.de/)
- 外部 API の呼び出しには User-Agent を明示しています

## ローカル起動

```bash
pip install -r requirements.txt
python app.py
```

起動したらブラウザで http://127.0.0.1:5000 を開いてください。

## テスト

```bash
python -m unittest discover -s tests
```

テストはネットワークへの実アクセスを行わず、API 呼び出しはモックで置き換えています。

## デプロイ（Render）

`render.yaml` を使って Web Service（Free）としてデプロイできます。起動コマンドは `gunicorn app:app`、ヘルスチェックは `/healthz` です。

## 構成

- `app.py` … 本体（単一ファイル）
- `tests/test_app.py` … unittest
- `requirements.txt` … Flask と gunicorn のみ（外部 API 呼び出しは標準ライブラリ urllib）
