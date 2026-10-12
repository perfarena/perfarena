public int maxArea(int[] height) {
    int left = 0, right = height.length - 1;
    int Max = 0, current = 0;

    while (left < right) {
        current = Math.min(height[left], height[right]) * (right - left);
        Max = Math.max(Max, current);

        if (height[left] < height[right]) {
            left++;
        } else {
            right--;
        }
    }

    return Max;
}