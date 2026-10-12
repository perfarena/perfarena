public int countWeakCharacters(int[][] properties) {
    if (properties == null || properties.length == 0) {
        return 0;
    }

    Arrays.sort(properties, (a, b) -> {
        if (a[0] != b[0]) {
            return b[0] - a[0]; // Sort by attacking power in descending order
        } else {
            return a[1] - b[1]; // If attacking power is equal, sort by defensive power in ascending order
        }
    });

    int weakCount = 0;
    int minDefense = Integer.MIN_VALUE;

    for (int[] property : properties) {
        if (property[1] > minDefense) {
            weakCount++;
            minDefense = Math.max(minDefense, property[1]);
        }
    }

    return weakCount;
}