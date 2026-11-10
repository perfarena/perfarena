while (right < n) {
    if (numbers[left] != 0 && numbers[right] != 0) {
        left++;
        right++;
    } else if (numbers[right] == 0) {
        int temp = numbers[left];
        numbers[left] = numbers[right];
        numbers[right] = temp;
        left++;
        right++;
    }
}