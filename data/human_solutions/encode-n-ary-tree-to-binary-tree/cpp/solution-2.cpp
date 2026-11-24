int numJewelsInStones(string jewels, string stones) {
    int counts[256] = {0};
    int jewelCount = 0;

    // Count occurrences of characters in jewels
    for (char c : jewels) {
        counts[c]++;
    }

    // Check stones for jewels
    for (char c : stones) {
        if (counts[c] > 0) {
            jewelCount++;
        }
    }

    return jewelCount;
}