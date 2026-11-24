import java.util.PriorityQueue;

public class LastStoneWeight {
    public int lastStoneWeight(int[] stones) {
        PriorityQueue<Integer> maxHeap = new PriorityQueue<>((a, b) -> b - a);

        for (int stone : stones) {
            maxHeap.add(stone);
        }

        while (maxHeap.size() > 1) {
            int heaviest1 = maxHeap.poll();
            int heaviest2 = maxHeap.poll();

            int difference = Math.abs(heaviest1 - heaviest2);
            if (difference != 0) {
                maxHeap.add(difference);
            }
        }

        return maxHeap.isEmpty() ? 0 : maxHeap.poll();
    }
}