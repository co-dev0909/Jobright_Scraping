using System.Security.Cryptography;
using System.Text;

namespace Kula.App;

internal static class PairingKeyStore
{
    public static string LoadOrCreate()
    {
        string directory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "KulaAPP");
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, "pairing-key.txt");

        if (File.Exists(path)) return ReadExisting(path);

        string key = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        string temporaryPath = Path.Combine(directory, $"pairing-key-{Guid.NewGuid():N}.tmp");
        try
        {
            File.WriteAllText(temporaryPath, key, new UTF8Encoding(false));
            try
            {
                File.Move(temporaryPath, path, overwrite: false);
                return key;
            }
            catch (IOException) when (File.Exists(path))
            {
                // Another instance may have created the key while we started.
                return ReadExisting(path);
            }
        }
        finally
        {
            if (File.Exists(temporaryPath)) File.Delete(temporaryPath);
        }
    }

    private static string ReadExisting(string path)
    {
        string key = File.ReadAllText(path).Trim();
        if (key.Length != 64 || key.Any(character => !Uri.IsHexDigit(character)))
        {
            // Do not silently rotate an existing key and break paired clients.
            throw new InvalidDataException("The saved pairing key is invalid.");
        }
        return key;
    }
}
