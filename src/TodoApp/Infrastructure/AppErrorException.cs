using System.Globalization;

namespace TodoApp.Infrastructure;

/// <summary>業務エラー（DD-01 §5）。文言は ErrorIds.Messages（DD-12）から引く。</summary>
public sealed class AppErrorException : Exception
{
    public AppErrorException(string errorId, params object[] args)
        : base(Format(errorId, args))
    {
        ErrorId = errorId;
        Args = args;
    }

    public string ErrorId { get; }

    public IReadOnlyList<object> Args { get; }

    private static string Format(string errorId, object[] args) =>
        string.Format(CultureInfo.InvariantCulture, ErrorIds.Messages[errorId], args);
}
