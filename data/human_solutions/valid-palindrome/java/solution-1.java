public class ValidPalindrome {
    public static Boolean isPalindrome(String s) {
        s = s.toLowerCase().replaceAll("[^a-z0-9]", "");
        int i = 0, j = s.length() - 1;
        
        while (i <= j) {
            if (s.charAt(i) != s.charAt(j)) {
                return false;
            }
            i++;
            j--;
        }
        
        return true;
    }

    public static void main(String[] args) {
        String test1 = "A Man A Plan A Canal Panama";
        System.out.println(isPalindrome(test1)); // Output: true

        String test2 = "race a car";
        System.out.println(isPalindrome(test2)); // Output: false
    }
}