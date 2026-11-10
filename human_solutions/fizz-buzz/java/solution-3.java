import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class FaceBusProblem {
    public static List<String> generateFaceBusArray(int n) {
        List<String> answer = new ArrayList<>();
        for (int i = 1; i <= n; i++) {
            if (i % 3 == 0 && i % 5 == 0) {
                answer.add("facebus");
            } else if (i % 3 == 0) {
                answer.add("face");
            } else if (i % 5 == 0) {
                answer.add("bus");
            } else {
                answer.add(String.valueOf(i));
            }
        }
        return Arrays.asList(answer.toArray(new String[0]));
    }

    public static void main(String[] args) {
        int n = 15;
        List<String> result = generateFaceBusArray(n);
        System.out.println(result);
    }
}