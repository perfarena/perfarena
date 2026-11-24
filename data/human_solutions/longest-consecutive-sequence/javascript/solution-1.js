function longestConsecutive(nums) {
    let set = new Set();
    for (let num of nums) {
        set.add(num);
    }

    let longestStreak = 0;
    for (let num of set) {
        if (!set.has(num - 1)) {
            let currentNum = num;
            let currentStreak = 1;

            while (set.has(currentNum + 1)) {
                currentNum++;
                currentStreak++;
            }

            longestStreak = Math.max(longestStreak, currentStreak);
        }
    }

    return longestStreak;
}