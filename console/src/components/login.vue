<template>
    <div class="wt-login-page">
        <div class="wt-login-brand"><span class="wt-login-mark">W<sup>+</sup></span><span>WonderTrader <strong>Plus</strong></span></div>
        <main class="wt-login-card">
            <div class="wt-login-eyebrow">YOUR QUANT WORKSPACE</div>
            <h1>回到你的交易工作台</h1>
            <p>研究、监控与执行，在同一个空间。</p>
            <form @submit.prevent="doLogin">
                <label for="wt-login-id">账户</label>
                <el-input id="wt-login-id" v-model="loginid" placeholder="用户名" autocomplete="username" size="large" required/>
                <label for="wt-login-password">密码</label>
                <el-input id="wt-login-password" v-model="passwd" placeholder="密码" type="password" autocomplete="current-password" size="large" show-password required/>
                <el-checkbox v-model="saveuser">保存用户名</el-checkbox>
                <el-button native-type="submit" type="primary" :loading="loading" size="large">登 录<span class="wt-login-arrow">→</span></el-button>
            </form>
            <div class="wt-login-note">连接到你的 WonderTrader 监控服务</div>
        </main>
        <footer>WONDERTRADER PLUS <span>量化研究 · 实时监控</span></footer>
    </div>
</template>

<script>
import { useSessionStore } from "@/store.js";
export default {
    name: "Login",
    data() {
        return {
            loginid: "",
            passwd: "",
            saveuser:true,
            loading: false
        };
    },
    methods: {
        doLogin: function () {
            if(this.loading) return;
            let self = this;
            let loginid = self.loginid;
            let passwd = self.passwd;
            self.loading = true;
            this.$api.login(loginid, passwd, (resObj) => {
                if (resObj.result < 0) {
                    self.$alert("用户登录出错：" + resObj.message, "登录失败");
                } else {
                    let userinfo = resObj.userinfo;
                    localStorage.setItem("last_user", loginid);
                    localStorage.setItem("save_user", self.saveuser?"true":"false");

                    useSessionStore().loginok({
                        isLogined: true,
                        loginid: loginid,
                        userinfo:userinfo
                    });

                    this.$push.connect()

                    const target=this.$route.query.redirect;
                    self.$router.push(typeof target==='string' && target.startsWith('/') && !target.startsWith('//') && !target.startsWith('/login')?target:'/index');
                }
                self.loading = false;
            });
        },
    },
    mounted() {
        this.saveuser = localStorage.getItem("save_user") == 'true';
        if(this.saveuser)
            this.loginid = localStorage.getItem("last_user");
    },
};
</script>


<style scoped>
.wt-login-page{height:100%;min-height:600px;background:#f7f8f4;display:flex;flex-direction:column;align-items:center;box-sizing:border-box;padding:36px 42px;}
.wt-login-brand{align-self:flex-start;display:flex;gap:12px;align-items:center;font-size:15px;color:#394233;letter-spacing:-.2px;}
.wt-login-brand strong{font-weight:500;color:#82916f;}
.wt-login-mark{display:inline-flex;width:34px;height:34px;align-items:center;justify-content:center;border-radius:10px;background:#282d25;color:white;font-size:20px;font-weight:600;letter-spacing:-2px;}
.wt-login-mark sup{font-size:12px;align-self:flex-start;letter-spacing:0;margin-top:5px;color:#c7d7b7;}
.wt-login-card{width:360px;max-width:100%;margin:auto 0;padding:36px 0 52px;}
.wt-login-eyebrow{color:#82916f;font-size:10px;font-weight:600;letter-spacing:2.4px;margin-bottom:18px;}
h1{font-weight:550;letter-spacing:-.6px;font-size:27px;margin:0 0 12px;color:#282f25;}
p{color:#8a9083;font-size:13px;margin:0 0 34px;}
form{display:flex;flex-direction:column;gap:10px;}
label{font-size:12px;font-weight:550;margin-top:6px;}
form :deep(.el-input__wrapper){background:#fff;border-radius:8px;box-shadow:0 0 0 1px #dfe3d8 inset;}
form :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 1px #6b805c inset;}
form .el-checkbox{margin:1px 0 12px;}
form :deep(.el-checkbox__label){font-size:12px;color:#7a8472;}
form .el-button{margin:0;height:44px;border-radius:8px;font-size:13px;}
.wt-login-arrow{margin-left:auto;font-size:17px;}
.wt-login-note{text-align:center;font-size:11px;color:#9b9f94;margin-top:24px;}
footer{align-self:stretch;display:flex;justify-content:space-between;color:#9b9f94;font-size:10px;letter-spacing:1.2px;}
footer span{letter-spacing:.5px;}
@media(max-width:600px){.wt-login-page{padding:24px;}.wt-login-brand{font-size:13px;}h1{font-size:24px;}}
</style>
