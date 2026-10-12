public int[] topKFrequent(int[] nums, int k) {
    // Create a frequency map
    Map<Integer, Integer> map = new HashMap<>();
    for (int i : nums) {
        map.put(i, map.getOrDefault(i, 0) + 1);
    }

    // Create a max heap with custom comparator
    PriorityQueue<Integer> maxHeap = new PriorityQueue<>((a, b) -> map.get(b) - map.get(a));
    
    // Fill the max heap
    for (int key : map.keySet()) {
        maxHeap.add(key);
    }

    // Create the result array
    int[] result = new int[k];
    for (int i = 0; i < k; i++) {
        result[i] = maxHeap.poll();
    }

    return result;
}