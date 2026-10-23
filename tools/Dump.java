import jadx.api.JadxArgs;
import jadx.api.JadxDecompiler;
import java.io.File;

public class Dump {
    public static void main(String[] a) throws Exception {
        JadxArgs args = new JadxArgs();
        args.setInputFile(new File(a[0]));
        args.setOutDir(new File(a[1]));
        args.setThreadsCount(8);
        try (JadxDecompiler j = new JadxDecompiler(args)) {
            j.load();
            j.save();
        }
    }
}
