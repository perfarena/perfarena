int findMaxConsecutiveOnes(vector<int>& nums, int k) {
    int answer = 0, count = 0;
    int left = 0;
    
    for (int right = 0; right < nums.size(); right++) {
        if (nums[right] == 0) {
            count++;
        }
        
        while (count > k) {
            if (nums[left] == 0) {
                count--;
            }
            left++;
        }
        
        answer = max(answer, right - left + 1);
    }
    
    return answer;
}