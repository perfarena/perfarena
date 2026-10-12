public int maxConsecutiveOnes(int[] nums, int k) {
    Queue<Integer> q = new LinkedList<>();
    int count = 0, max = 0;
    
    for (int i = 0; i < nums.length; i++) {
        count++;
        max = Math.max(max, count);
        
        if (nums[i] >= nums.length) break;
        
        if (k > 0) {
            count++;
            q.add(i);
            k--;
        }
        
        if (k == 0 || q.isEmpty()) {
            count = i - q.remove();
        }
        
        max = Math.max(max, count);
    }
    
    return max;
}