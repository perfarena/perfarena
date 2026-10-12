public class Solution {
    public boolean halvesAreAlike(String s) {
        int length = s.length();
        String vowels = "aeiouAEIOU";
        
        int countA = countVowels(s.substring(0, length / 2), vowels);
        int countB = countVowels(s.substring(length / 2), vowels);
        
        return countA == countB;
    }
    
    private int countVowels(String str, String vowels) {
        int count = 0;
        for (char c : str.toCharArray()) {
            if (vowels.indexOf(c) != -1) {
                count++;
            }
        }
        return count;
    }
}