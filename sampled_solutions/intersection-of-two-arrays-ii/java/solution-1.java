public int[] intersection(int[] nums1, int[] nums2) {
    int[] Ras = new int[Math.min(nums1.length, nums2.length)];
    int k = 0;
    
    // Find common elements and store in Ras
    for (int i = 0; i < nums1.length; i++) {
        for (int j = 0; j < nums2.length; j++) {
            if (nums1[i] == nums2[j]) {
                Ras[k++] = nums1[i];
                break;
            }
        }
    }
    
    // Create result array with correct length
    int[] result = new int[k];
    
    // Copy elements from Ras to result
    for (int i = 0; i < k; i++) {
        result[i] = Ras[i];
    }
    
    return result;
}