export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  public: {
    Tables: {
      api_call_events: {
        Row: {
          created_at: string
          endpoint: string
          id: string
          latency_ms: number
          status_code: number
          user_id: string
        }
        Insert: {
          created_at?: string
          endpoint: string
          id?: string
          latency_ms?: number
          status_code?: number
          user_id: string
        }
        Update: {
          created_at?: string
          endpoint?: string
          id?: string
          latency_ms?: number
          status_code?: number
          user_id?: string
        }
        Relationships: []
      }
      api_keys: {
        Row: {
          created_at: string
          id: string
          key_prefix: string
          key_suffix: string
          last_used_at: string | null
          name: string
          status: string
          updated_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          key_prefix: string
          key_suffix?: string
          last_used_at?: string | null
          name: string
          status?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          id?: string
          key_prefix?: string
          key_suffix?: string
          last_used_at?: string | null
          name?: string
          status?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      datasets: {
        Row: {
          columns: number
          created_at: string
          description: string
          file_size: string
          format: string
          id: string
          name: string
          project_id: string | null
          quality_score: number
          rows: number
          updated_at: string
          user_id: string
        }
        Insert: {
          columns?: number
          created_at?: string
          description?: string
          file_size?: string
          format?: string
          id?: string
          name: string
          project_id?: string | null
          quality_score?: number
          rows?: number
          updated_at?: string
          user_id: string
        }
        Update: {
          columns?: number
          created_at?: string
          description?: string
          file_size?: string
          format?: string
          id?: string
          name?: string
          project_id?: string | null
          quality_score?: number
          rows?: number
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "datasets_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
      deployed_endpoints: {
        Row: {
          avg_latency_ms: number
          burst_limit: number
          created_at: string
          endpoint_url: string
          error_rate: number
          id: string
          model_id: string | null
          model_name: string
          project_name: string
          rate_limit_per_min: number
          requests_per_min: number
          status: string
          updated_at: string
          uptime: number
          user_id: string
        }
        Insert: {
          avg_latency_ms?: number
          burst_limit?: number
          created_at?: string
          endpoint_url: string
          error_rate?: number
          id?: string
          model_id?: string | null
          model_name: string
          project_name?: string
          rate_limit_per_min?: number
          requests_per_min?: number
          status?: string
          updated_at?: string
          uptime?: number
          user_id: string
        }
        Update: {
          avg_latency_ms?: number
          burst_limit?: number
          created_at?: string
          endpoint_url?: string
          error_rate?: number
          id?: string
          model_id?: string | null
          model_name?: string
          project_name?: string
          rate_limit_per_min?: number
          requests_per_min?: number
          status?: string
          updated_at?: string
          uptime?: number
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "deployed_endpoints_model_id_fkey"
            columns: ["model_id"]
            isOneToOne: false
            referencedRelation: "trained_models"
            referencedColumns: ["id"]
          },
        ]
      }
      profiles: {
        Row: {
          avatar_url: string | null
          created_at: string
          display_name: string | null
          id: string
          updated_at: string
          user_id: string
        }
        Insert: {
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          id?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          avatar_url?: string | null
          created_at?: string
          display_name?: string | null
          id?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      projects: {
        Row: {
          base_model: string
          created_at: string
          credits_cost: number
          dataset_size: number
          description: string
          epochs: number
          id: string
          learning_rate: number
          name: string
          pinned: boolean
          progress: number
          status: string
          tags: string[]
          task_type: string
          updated_at: string
          user_id: string
        }
        Insert: {
          base_model: string
          created_at?: string
          credits_cost?: number
          dataset_size?: number
          description?: string
          epochs?: number
          id?: string
          learning_rate?: number
          name: string
          pinned?: boolean
          progress?: number
          status?: string
          tags?: string[]
          task_type: string
          updated_at?: string
          user_id: string
        }
        Update: {
          base_model?: string
          created_at?: string
          credits_cost?: number
          dataset_size?: number
          description?: string
          epochs?: number
          id?: string
          learning_rate?: number
          name?: string
          pinned?: boolean
          progress?: number
          status?: string
          tags?: string[]
          task_type?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      trained_models: {
        Row: {
          accuracy: number
          base_model: string
          created_at: string
          f1_score: number
          file_size: string
          format: string
          id: string
          latency_ms: number
          name: string
          precision: number
          project_id: string | null
          recall: number
          status: string
          task_type: string
          updated_at: string
          user_id: string
        }
        Insert: {
          accuracy?: number
          base_model: string
          created_at?: string
          f1_score?: number
          file_size?: string
          format?: string
          id?: string
          latency_ms?: number
          name: string
          precision?: number
          project_id?: string | null
          recall?: number
          status?: string
          task_type: string
          updated_at?: string
          user_id: string
        }
        Update: {
          accuracy?: number
          base_model?: string
          created_at?: string
          f1_score?: number
          file_size?: string
          format?: string
          id?: string
          latency_ms?: number
          name?: string
          precision?: number
          project_id?: string | null
          recall?: number
          status?: string
          task_type?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "trained_models_project_id_fkey"
            columns: ["project_id"]
            isOneToOne: false
            referencedRelation: "projects"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      seed_user_demo_data: { Args: { _user_id: string }; Returns: undefined }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
