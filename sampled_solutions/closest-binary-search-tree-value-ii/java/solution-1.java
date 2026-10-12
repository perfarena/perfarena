import java.util.PriorityQueue;
import java.util.Comparator;
import java.util.List;
import java.util.ArrayList;

public class ClosestBinaryTreeValue {
    public List<Integer> closestKValues(TreeNode root, double target, int k) {
        PriorityQueue<Integer> pq = new PriorityQueue<>(new Comparator<Integer>() {
            @Override
            public int compare(Integer a, Integer b) {
                double distanceA = Math.abs(a - target);
                double distanceB = Math.abs(b - target);
                if (distanceA < distanceB) return -1;
                else if (distanceA > distanceB) return 1;
                else return 0;
            }
        });

        helper(root, pq, k);

        List<Integer> result = new ArrayList<>();
        while (!pq.isEmpty()) {
            result.add(pq.poll());
        }

        return result;
    }

    private void helper(TreeNode root, PriorityQueue<Integer> pq, int k) {
        if (root == null) return;

        pq.add(root.val);

        if (pq.size() > k) {
            pq.poll();
        }

        helper(root.left, pq, k);
        helper(root.right, pq, k);
    }

    public static void main(String[] args) {
        // Test your implementation here
    }
}