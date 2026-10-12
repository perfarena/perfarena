public class ReverseWordsInString {
    public static String reverseWords(String s) {
        String[] words = s.split("\\s+");
        StringBuilder sb = new StringBuilder();

        for (int i = words.length - 1; i >= 0; i--) {
            sb.append(words[i]).append(" ");
        }

        String reversedString = sb.toString().trim();
        return reversedString;
    }

    public static void main(String[] args) {
        String input = "the sky is blue";
        System.out.println(reverseWords(input)); // Output: "blue is sky"

        input = "  hello world  ";
        System.out.println(reverseWords(input)); // Output: "world hello"

        input = "a   good   example";
        System.out.println(reverseWords(input)); // Output: "example good a"
    }
}