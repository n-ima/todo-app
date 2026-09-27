using System.Collections.Frozen;

namespace TodoApp.Infrastructure;

/// <summary>DD-12 のエラー ID と文言（1 対 1）。文言中の {0} 等は DD-12 の差し込み値を出現順に番号化したもの。</summary>
public static class ErrorIds
{
    public const string AuthFailed = "E-AUTH-FAILED";
    public const string AuthLocked = "E-AUTH-LOCKED";
    public const string AuthRequired = "E-AUTH-REQUIRED";
    public const string AuthMustChange = "E-AUTH-MUST-CHANGE";
    public const string PwdCurrent = "E-PWD-CURRENT";
    public const string PwdLength = "E-PWD-LENGTH";
    public const string PwdConfirm = "E-PWD-CONFIRM";
    public const string PwdSame = "E-PWD-SAME";
    public const string UserDuplicate = "E-USER-DUPLICATE";
    public const string UserLoginidFormat = "E-USER-LOGINID-FORMAT";
    public const string UserLastAdmin = "E-USER-LAST-ADMIN";
    public const string PermDenied = "E-PERM-DENIED";
    public const string PermSubtree = "E-PERM-SUBTREE";
    public const string PermAdminOnly = "E-PERM-ADMIN-ONLY";
    public const string TaskNotFound = "E-TASK-NOT-FOUND";
    public const string TaskTitleRequired = "E-TASK-TITLE-REQUIRED";
    public const string TaskTitleLength = "E-TASK-TITLE-LENGTH";
    public const string TaskDateOrder = "E-TASK-DATE-ORDER";
    public const string TaskAssigneeInactive = "E-TASK-ASSIGNEE-INACTIVE";
    public const string TaskChildrenOpen = "E-TASK-CHILDREN-OPEN";
    public const string TaskCycle = "E-TASK-CYCLE";
    public const string TaskParentGone = "E-TASK-PARENT-GONE";
    public const string MemoTooLong = "E-MEMO-TOO-LONG";
    public const string Validation = "E-VALIDATION";
    public const string ProjectName = "E-PROJECT-NAME";
    public const string LockHeld = "E-LOCK-HELD";
    public const string LockHeldSubtree = "E-LOCK-HELD-SUBTREE";
    public const string LockLost = "E-LOCK-LOST";
    public const string TrashNotFound = "E-TRASH-NOT-FOUND";
    public const string TrashParentMissing = "E-TRASH-PARENT-MISSING";
    public const string NotifyTaskDeleted = "E-NOTIFY-TASK-DELETED";
    public const string WfNameDuplicate = "E-WF-NAME-DUPLICATE";
    public const string WfInUse = "E-WF-IN-USE";
    public const string WfNeedDone = "E-WF-NEED-DONE";
    public const string WfLastStatus = "E-WF-LAST-STATUS";
    public const string Csrf = "E-CSRF";
    public const string DbBusy = "E-DB-BUSY";
    public const string SysUnexpected = "E-SYS-UNEXPECTED";
    public const string SysSchemaMismatch = "E-SYS-SCHEMA-MISMATCH";
    public const string BackupSnapshot = "E-BACKUP-SNAPSHOT";
    public const string BackupSecret = "E-BACKUP-SECRET";
    public const string BackupShare = "E-BACKUP-SHARE";
    public const string RestoreDecrypt = "E-RESTORE-DECRYPT";
    public const string RestoreIntegrity = "E-RESTORE-INTEGRITY";
    public const string RestoreNewerSchema = "E-RESTORE-NEWER-SCHEMA";
    public const string CliServiceRunning = "E-CLI-SERVICE-RUNNING";

    public static readonly FrozenDictionary<string, string> Messages = new Dictionary<string, string>
    {
        [AuthFailed] = "ログイン ID またはパスワードが正しくありません",
        [AuthLocked] = "ログインに続けて失敗したため、15 分間ログインできません。時間をおいてからもう一度お試しください",
        [AuthRequired] = "ログインしてください（画面はログインへ転送）",
        [AuthMustChange] = "初期パスワードを変更してください",
        [PwdCurrent] = "現在のパスワードが正しくありません",
        [PwdLength] = "パスワードは 8 文字以上 128 文字以下にしてください",
        [PwdConfirm] = "確認用のパスワードが一致しません",
        [PwdSame] = "現在（初期）のパスワードとは別のパスワードにしてください",
        [UserDuplicate] = "このログイン ID は既に使われています",
        [UserLoginidFormat] = "ログイン ID は半角英数字と . _ - の 50 文字以内で入力してください",
        [UserLastAdmin] = "管理者が 1 人もいなくなります",
        [PermDenied] = "このタスクを編集できるのは担当者と管理者だけです",
        [PermSubtree] = "配下に、あなたが編集できないタスクがあるため操作できません: {0}",
        [PermAdminOnly] = "この操作は管理者だけが行えます",
        [TaskNotFound] = "タスクが見つかりません（削除された可能性があります）",
        [TaskTitleRequired] = "タイトルは必須です",
        [TaskTitleLength] = "タイトルは 200 文字以内にしてください",
        [TaskDateOrder] = "期限は開始日以降にしてください",
        [TaskAssigneeInactive] = "無効化された利用者は担当者に設定できません",
        [TaskChildrenOpen] = "未完了の子タスクが {0} 件あります。完了にしますか",
        [TaskCycle] = "自分自身や配下のタスクの下には移動できません",
        [TaskParentGone] = "移動先（親）が見つかりません。画面を読み込み直してください",
        [MemoTooLong] = "メモは 100,000 文字以内にしてください（現在 {0} 文字）",
        [Validation] = "入力内容を確認してください: {0}",
        [ProjectName] = "プロジェクト名は 1〜100 文字で入力してください",
        [LockHeld] = "{0}さんが編集中です（{1} から）",
        [LockHeldSubtree] = "{0}さんが編集中のタスク『{1}』が含まれるため操作できません",
        [LockLost] = "ロックが解除されています。入力内容は下書きとして残しました。もう一度「編集する」を押してください",
        [TrashNotFound] = "このタスクはゴミ箱にありません（完全に削除された可能性があります）",
        [TrashParentMissing] = "元の親が存在しません。先に親を戻すか、管理者に相談してください",
        [NotifyTaskDeleted] = "このタスクは削除されています",
        [WfNameDuplicate] = "同じ名前の状態が既にあります",
        [WfInUse] = "この状態のタスクが {0} 件あります（ゴミ箱を含む）",
        [WfNeedDone] = "完了扱いの状態が 1 つ以上必要です",
        [WfLastStatus] = "状態を 1 つ以上残してください",
        [Csrf] = "画面の有効期限が切れました。画面を読み込み直してから、もう一度操作してください",
        [DbBusy] = "混み合っています。少し待ってからもう一度操作してください",
        [SysUnexpected] = "予期しないエラーが発生しました。管理者にお知らせください（相関 ID: {0}）",
        [SysSchemaMismatch] = "データベースの版（{0}）がアプリの版（{1}）と一致しません。更新スクリプトを使ってください",
        [BackupSnapshot] = "データベースのコピーに失敗しました",
        [BackupSecret] = "バックアップ鍵を読み込めません",
        [BackupShare] = "共有フォルダに接続または書き込みできません",
        [RestoreDecrypt] = "復号できません（鍵が違うか、ファイルが壊れています）",
        [RestoreIntegrity] = "バックアップの内容が壊れています",
        [RestoreNewerSchema] = "このバックアップは新しい版で作られています",
        [CliServiceRunning] = "サービス TodoApp が動いています。停止してから実行してください",
    }.ToFrozenDictionary();

    /// <summary>DD-12 の HTTP ステータス（4xx/5xx のもの）。/error が AppErrorException を応答へ変換するときに使う。無いものは 500。</summary>
    public static readonly FrozenDictionary<string, int> HttpStatuses = new Dictionary<string, int>
    {
        [AuthRequired] = 401,
        [AuthMustChange] = 403,
        [PermDenied] = 403,
        [PermSubtree] = 403,
        [PermAdminOnly] = 403,
        [TaskNotFound] = 404,
        [TaskTitleRequired] = 400,
        [TaskTitleLength] = 400,
        [TaskDateOrder] = 400,
        [TaskAssigneeInactive] = 400,
        [TaskChildrenOpen] = 409,
        [TaskCycle] = 400,
        [TaskParentGone] = 409,
        [MemoTooLong] = 400,
        [Validation] = 400,
        [ProjectName] = 400,
        [LockHeld] = 409,
        [LockHeldSubtree] = 409,
        [LockLost] = 409,
        [TrashNotFound] = 404,
        [TrashParentMissing] = 409,
        [WfNameDuplicate] = 400,
        [WfInUse] = 409,
        [WfNeedDone] = 409,
        [WfLastStatus] = 409,
        [Csrf] = 400,
        [DbBusy] = 503,
        [SysUnexpected] = 500,
    }.ToFrozenDictionary();
}
