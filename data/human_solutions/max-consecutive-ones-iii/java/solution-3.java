int lastStoneWeight(int[] stones) {
    int n = stones.length;
    while (n > 1) {
        Arrays.sort(stones);
        int highest = stones[n - 1];
        int secondHighest = stones[n - 2];
        
        if (highest == secondHighest) {
            n -= 2;
        } else {
            stones[n - 2] = highest - secondHighest;
            n--;
        }
    }
    
    if (n == 1) {
        return stones[0];
    } else {
        return 0;
    }
}