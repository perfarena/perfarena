public int countJewelsInStones(String jewels, String stones) {
    int count = 0;
    Set<Character> jewelSet = new HashSet<>();
    
    // Add characters of jewels to the set
    for (char ch : jewels.toCharArray()) {
        jewelSet.add(ch);
    }
    
    // Check stones against jewels set
    for (char ch : stones.toCharArray()) {
        if (jewelSet.contains(ch)) {
            count++;
        }
    }
    
    return count;
}