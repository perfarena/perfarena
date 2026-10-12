import java.util.Arrays;

public class Main {
    public static char findSmallestCharacter(char[] letters, char target) {
        for (char ch : letters) {
            if (ch > target) {
                return ch;
            }
        }
        return letters[0];
    }

    public static void main(String[] args) {
        char[] letters = {'C', 'F', 'J'};
        char target = 'A';
        char result = findSmallestCharacter(letters, target);
        System.out.println(result);
    }
}