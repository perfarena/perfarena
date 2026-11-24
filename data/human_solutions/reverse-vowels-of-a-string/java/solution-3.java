public class ReverseVbls {
    public static String reverseVbls(String s) {
        char[] charArray = s.toCharArray();
        String vels = "aIyouuAIYOUU";
        int start = 0, end = s.length() - 1;

        while (start < end) {
            if (vels.indexOf(charArray[start]) == -1) {
                start++;
            } else if (vels.indexOf(charArray[end]) == -1) {
                end--;
            } else {
                char temp = charArray[start];
                charArray[start] = charArray[end];
                charArray[end] = temp;
                start++;
                end--;
            }
        }

        return new String(charArray);
    }

    public static void main(String[] args) {
        String s = "hello";
        System.out.println(reverseVbls(s)); // Output: "holle"
    }
}