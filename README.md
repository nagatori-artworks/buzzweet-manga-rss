# Buzzweet 漫画ツイートRSS（非公式・個人用）

[Buzzweetの漫画ツイート一覧](https://buzzweet.com/manga-tweet/)から、Xの投稿1件につきRSSの記事1件を生成するツールです。GitHub Actionsで定期実行し、GitHub PagesでRSSを配信します。

**このテンプレートは、2026-11-21時点のBuzzweetの表示構造を手がかりに実装した初版です。** こちらの環境からはBuzzweetの生HTMLを取得できず、実際のサイトに対する統合テストは未実施です。GitHub Actionsの初回実行時にHTML解析を確認し、必要なら修正してください。実サイトでの取得成功を保証するものではありません。

## 1. できること

- RSS記事タイトル：X投稿の本文テキスト（プログラムでは省略しません）
- RSSの記事リンク：Buzzweetではなく、Xの元ポストへのURL
- RSS記事の説明文：`表示名｜Buzzweet 漫画ツイートRSS`。表示名が取れない場合は共通文言
- RSS画像：**1枚目の写真**を `media:thumbnail`、`media:content`、`content:encoded` の3形式で出力。FxEmbed APIを優先し、Buzzweetにメディア画像があればフォールバック
- 1つのRSSに複数アカウントの投稿をまとめる
- X投稿IDを使って重複を防止し、投稿の初検出日時を記事日時に使用
- 2時間ごとに1ページ目を確認。前回の最新投稿が出るまで2、3ページ目も確認。目印が見つかったページは最後まで解析
- 日本時間で1日1回、**最大3ページすべて**を確認
- RSSは直近250件、既知投稿IDは直近5000件を保持
- 途中でHTML取得・解析に失敗したら、公開済みのRSSを壊さず、前回のXMLを維持（初回取得失敗は実行失敗）
- 漫画画像そのものはGitHubには保存しません。公開先に画像URLだけを書きます

`media:thumbnail`などの画像指定に対応していても、Inoreader側が既読記事の画像を再取得しないことがあります。必要なら新規投稿で表示を確認してください。外部画像サイトやFxEmbedの制限によって表示できない場合もあります。

## 2. Mac＋GitHub Desktopでの導入

### A. リポジトリ作成

1. このZIPをダウンロード・解凍します。`buzzweet-manga-rss` フォルダができます。
2. GitHub Desktop → `File` → `New Repository...` を選択します。
3. `Name`を **`buzzweet-manga-rss`** にします。`Local Path`は普段のGitHub作業用フォルダでOKです。
4. `Description`は `Personal RSS for Buzzweet manga tweets`（省略可）。`Initialize this repository with a README` のチェックは **外す**、`Git Ignore` は `None`、`License` は `None`。
5. `Create Repository` を押します。
6. 解凍した `buzzweet-manga-rss` **フォルダの中身だけを**、GitHub Desktopが作った `buzzweet-manga-rss` フォルダにコピーします。**フォルダを二重に入れない**でください。
7. macOSのFinderで `Command + Shift + .` を押して隠しファイルを表示し、`.github` フォルダがコピーされたことを確認します（重要）。GitHub Desktop作成の `.git` フォルダを上書き・削除しないでください。
8. GitHub Desktopへ戻り、変更ファイル一覧に `generate.py`、`README.md`、`.github/workflows/update.yml` などが含まれることを確認します。
9. Summary欄に `Initial Buzzweet RSS generator` を入力し、**Commit to main** を押します。違うブランチ名の場合は `main` に変更してください。
10. **Publish repository** → `Keep this code private` のチェックを **外す（Public）** → Publish。

### B. GitHub Pagesを公開する

1. Webブラウザで GitHub の `buzzweet-manga-rss` リポジトリを開きます。
2. `Settings` → 左側の `Pages` → `Build and deployment` の `Source` を **GitHub Actions** に変更します。
3. `Actions` タブ → 左側の `Update Buzzweet manga RSS` → `Run workflow` を押して初回実行します。GitHub側の既定設定によっては最初のPushでも実行が開始します。
4. 緑のチェックで成功するか確認します。失敗した場合は該当実行の `Unit tests` / `Fetch Buzzweet and generate RSS` のログを見てください。
5. GitHub PagesのURLを開き、XMLをInoreaderで購読します。

たとえばGitHubユーザー名が `exampleuser` の場合：

- Web確認ページ：`https://exampleuser.github.io/buzzweet-manga-rss/`
- RSS：`https://exampleuser.github.io/buzzweet-manga-rss/manga.xml`

公開URLは**例**です。実際のアカウント名を当てはめてください。

### C. ChatGPTのGitHub連携から見てもらう場合

ChatGPT用のGitHubアプリに「選択したリポジトリだけ」を許可している場合、新しいリポジトリを作成しただけではアクセス権は自動追加されません。

GitHubの右上プロフィールアイコン → `Settings` → `Applications` → `Installed GitHub Apps` で、ChatGPT/OpenAIに接続したGitHubアプリの `Configure` を開きます。`Repository access` の `Only select repositories` に **`buzzweet-manga-rss`** を追加して保存します。はてなRSSの許可は外さず、追加する形でかまいません。画面の名称はGitHub側の変更で多少異なります。

その後ChatGPTに「`buzzweet-manga-rss` を確認して」と伝えると、許可が反映されていればリポジトリの内容を確認できます。

## 3. GitHub Actionsの設定

`.github/workflows/update.yml` に記載済みです。UTCの2時間ごとにスケジュール実行（時刻はベストエフォート）します。`main`へのPush、手動実行でも起動します。

実行が失敗したときはGitHubの `Actions` タブに理由が表示されます。GitHub Pagesの設定漏れや`git push`の権限不足の場合、`Settings → Actions → General → Workflow permissions`の設定を確認してください。

**注意：GitHub Actionsは指定時刻から遅延・省略することがあります。Publicリポジトリが60日間非アクティブだとスケジュール実行が停止する場合があります。定期的に実行履歴や公開ページを確認してください。**

## 4. 初回のチェックポイント

- Buzzweetへのアクセスに成功したか
- **1ページあたり3件以上**のメインポストを解析できたか（返信や引用元を別記事にしていないか）
- 作成されたRSSに元投稿本文・元X投稿URLがあるか
- `新規投稿のサムネイル取得: 〇/〇件` に何件出るか
- Inoreaderで画像付きカードとして表示できるか

最初のGitHub Actions実行で特に確認したいのは、`parse_buzzweet()` がBuzzweetの**実際のHTML**に合っているかどうかです。HTMLの構造が違った場合はテキスト抽出処理を修正します。プログラムは記事数が少なすぎると公開を止め、空のRSSで既存データを上書きしないようにしています。

## 5. ソースファイル

- `generate.py`：取得、ページ送り、画像補完、RSS作成
- `tests/test_generate.py`：HTML例とRSS形式の単体テスト
- `.github/workflows/update.yml`：自動実行・GitHub Pages配信
- `requirements.txt`：Pythonの依存ライブラリ
- `site/`：公開するRSSや確認ページの出力先
- `state.json`：初回実行時に自動作成される、重複防止・巡回状態の保存ファイル

## 6. メンテナンス時の注意

GitHub Actionsが自動的に `state.json` と `site/` のファイルをコミットするため、ローカルでソースを編集する前にGitHub Desktopで **Fetch origin → Pull origin** してから編集してください。編集後は **Commit → Push origin** します。

サイトの利用規約やアクセス制限を尊重し、取得頻度を過度に増やさないでください。Buzzweet・X・FxEmbedのいずれとも提携関係のない非公式個人用ツールです。
