public int[] intersection(int[] nums1, int[] nums2) {
    Set<Integer> set = new HashSet<>();
    for (int num : nums1) {
        set.add(num);
    }
    
    List<Integer> answer = new ArrayList<>();
    for (int num : nums2) {
        if (set.contains(num)) {
            answer.add(num);
            set.remove(num);
        }
    }
    
    int[] result = new int[answer.size()];
    for (int i = 0; i < answer.size(); i++) {
        result[i] = answer.get(i);
    }
    
    return result;
}