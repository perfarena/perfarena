public boolean halvesAreAlike(String s) {
    Set<Character> vowels = new HashSet<>(Arrays.asList('a', 'e', 'i', 'o', 'u', 'A', 'E', 'I', 'O', 'U'));
    char[] chars = s.toCharArray();
    int left = 0, right = s.length() - 1;
    int leftCount = 0, rightCount = 0;

    while (left < right) {
        if (vowels.contains(chars[left])) {
            leftCount++;
        }
        if (vowels.contains(chars[right])) {
            rightCount++;
        }
        left++;
        right--;
    }

    return leftCount == rightCount;
}